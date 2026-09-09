"""Plan-time binding projection and read-only generation resolution."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from novelvideo.narrative_groups.reference_decisions import (
    ReferenceDecisionSnapshot,
)
from novelvideo.narrative_groups.reference_uploads import (
    InvalidReferenceUpload,
    ReferenceUpload,
    validate_reference_image,
)
from novelvideo.production_workflow import AdoptionStatus, ProductionWorkflowStore
from novelvideo.production_workflow.store import production_workflow_project_lock

from novelvideo.production_workflow.slot_ids import (
    character_state_slot_id,
    prop_reference_slot_id,
    scene_base_slot_id,
    scene_state_slot_id,
)

from .planned_bindings import AssetKind, BindingStatus, PlannedReferenceBinding
from .reference_requirements import structured_scene_requirement


class PlannedReferenceError(ValueError):
    """Base class for stable, API-safe planned-reference failures."""

    error_code = "INVALID_PLANNED_REFERENCE"


class PlannedReferencesRequired(PlannedReferenceError):
    error_code = "PLANNED_REFERENCES_REQUIRED"


class UnresolvedPlannedReference(PlannedReferenceError):
    error_code = "UNRESOLVED_PLANNED_REFERENCE"


class StaleReferenceBinding(PlannedReferenceError):
    error_code = "STALE_REFERENCE_BINDING"


class InvalidPlannedReference(PlannedReferenceError):
    error_code = "INVALID_PLANNED_REFERENCE"


class PlannedBindingStore(Protocol):
    async def list_planned_reference_bindings(
        self, episode_number: int, group_id: str | None = None
    ) -> list[PlannedReferenceBinding]: ...


class ReadOnlyPlannedBindingStore:
    """Read bindings without opening SQLite in migration/write mode."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).resolve(strict=False)

    async def list_planned_reference_bindings(
        self, episode_number: int, group_id: str | None = None
    ) -> list[PlannedReferenceBinding]:
        if not self.database_path.is_file():
            return []
        uri = f"{self.database_path.as_uri()}?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "SELECT * FROM planned_reference_bindings "
                    "WHERE episode_number = ? ORDER BY binding_id",
                    (episode_number,),
                ).fetchall()
        except sqlite3.Error:
            return []
        bindings = [
            PlannedReferenceBinding(
                binding_id=row["binding_id"],
                project_id=row["project_id"],
                episode_number=row["episode_number"],
                source_plan_revision_id=row["source_plan_revision_id"],
                asset_kind=row["asset_kind"],
                entity_id=row["entity_id"],
                base_entity_id=row["base_entity_id"],
                variant_id=row["variant_id"],
                asset_slot_id=row["asset_slot_id"],
                group_ids=tuple(json.loads(row["group_ids_json"])),
                beat_ids=tuple(json.loads(row["beat_ids_json"])),
                shot_ids=tuple(json.loads(row["shot_ids_json"])),
                required=bool(row["required"]),
                status=row["status"],
                resolution=row["resolution"],
                display_label=row["display_label"],
            )
            for row in rows
        ]
        if group_id is None:
            return bindings
        return [item for item in bindings if group_id in item.group_ids]


class ResolvedPlannedReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    binding_id: str
    asset_kind: AssetKind
    entity_id: str
    display_label: str
    variant_id: str = ""
    beat_ids: tuple[str, ...] = ()
    required: bool
    status: BindingStatus
    selected_by_default: bool
    asset_slot_id: str
    version_id: str = ""
    adoption_status: str = ""
    thumbnail_url: str = ""
    warning: str = ""
    relative_path: str = ""
    sha256: str = ""
    group_ids: tuple[str, ...] = ()
    shot_ids: tuple[str, ...] = ()


class PlannedReferencePreview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reference_revision: str = Field(min_length=1)
    bindings: tuple[ResolvedPlannedReference, ...]
    max_images: int = Field(gt=0)


@dataclass(frozen=True, slots=True)
class PlannedSnapshotReferenceImage:
    requirement_id: str
    source: Literal["matched", "upload"]
    source_id: str
    asset_kind: str
    image_path: str
    resolution: Literal["matched", "temporary"]
    entity_id: str = ""
    shot_ids: tuple[str, ...] = ()
    binding_id: str = ""
    asset_slot_id: str = ""
    version_id: str = ""
    relative_path: str = ""
    sha256: str = ""
    group_ids: tuple[str, ...] = ()
    beat_ids: tuple[str, ...] = ()
    project_id: str = ""
    episode_number: int = 0


def _get(value: Any, name: str, default: Any = "") -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _has(value: Any, name: str) -> bool:
    return name in value if isinstance(value, Mapping) else hasattr(value, name)


def _items(values: Iterable[Any] | Mapping[Any, Any]) -> tuple[Any, ...]:
    if isinstance(values, Mapping):
        return tuple(values.values())
    return tuple(values)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _append_unique(
    values: tuple[str, ...], additions: Iterable[str]
) -> tuple[str, ...]:
    result = list(values)
    for addition in additions:
        item = _text(addition)
        if item and item not in result:
            result.append(item)
    return tuple(result)


def _identities(character: Any) -> tuple[Any, ...]:
    identities = _get(character, "identities", None)
    if identities is not None:
        return _items(identities)
    return ()


def _identity_phase_key(identity_id: str) -> str:
    if identity_id.endswith("_青年时期"):
        return identity_id.removesuffix("时期") + "期"
    return identity_id


def _character_identity_candidates(
    entity_key: str,
    characters: tuple[Any, ...],
    episode_identity_ids: frozenset[str],
    identity_default_map: Mapping[str, str],
) -> tuple[list[tuple[Any, Any]], bool]:
    exact = [
        (character, identity)
        for character in characters
        for identity in _identities(character)
        if _text(_get(identity, "identity_id")) == entity_key
    ]
    if exact:
        return exact, False

    named_characters = [
        character
        for character in characters
        if _text(_get(character, "name")) == entity_key
    ]
    if named_characters:
        episode_candidates = [
            (character, identity)
            for character in named_characters
            for identity in _identities(character)
            if _text(_get(identity, "identity_id")) in episode_identity_ids
        ]
        if entity_key in identity_default_map:
            default_id = _text(identity_default_map[entity_key])
            default_candidates = [
                candidate
                for candidate in episode_candidates
                if _text(_get(candidate[1], "identity_id")) == default_id
            ]
            if len(default_candidates) == 1:
                return default_candidates, False
            return episode_candidates, True
        if not episode_identity_ids:
            return [
                (character, identity)
                for character in named_characters
                for identity in _identities(character)
                if _text(_get(identity, "identity_id"))
            ], False
        return episode_candidates, False

    phase_key = _identity_phase_key(entity_key)
    return (
        [
            (character, identity)
            for character in characters
            for identity in _identities(character)
            if _text(_get(identity, "identity_id")) in episode_identity_ids
            and _identity_phase_key(_text(_get(identity, "identity_id"))) == phase_key
        ],
        False,
    )


def _explicitly_missing_image(entity: Any, *, identity: bool = False) -> bool:
    if _get(entity, "has_reference_image", None) is False:
        return True
    if _text(_get(entity, "image_status", "")) in {"missing", "missing_image"}:
        return True
    if identity:
        if not _has(entity, "reference_images"):
            return False
        return not any(
            _text(path) for path in (_get(entity, "reference_images", ()) or ())
        )

    image_fields = (
        "reference_images",
        "reference_image",
        "master_image",
        "image_path",
    )
    present = [name for name in image_fields if _has(entity, name)]
    if not present:
        return False
    return not any(
        any(_text(path) for path in value)
        if isinstance(value, (list, tuple))
        else _text(value)
        for name in present
        for value in (_get(entity, name, ""),)
    )


@dataclass(frozen=True)
class _ProjectedRequirement:
    kind: AssetKind
    entity_key: str
    base_entity_id: str = ""
    variant_id: str = ""
    group_ids: tuple[str, ...] = ()
    beat_ids: tuple[str, ...] = ()
    shot_ids: tuple[str, ...] = ()
    required: bool = True
    malformed_scene_state: bool = False

    @property
    def key(self) -> tuple[str, str, str]:
        if self.kind == "scene_variant" and self.malformed_scene_state:
            return self.kind, "", self.entity_key
        if self.kind == "scene_variant":
            return self.kind, self.base_entity_id, self.variant_id
        return self.kind, self.entity_key, ""


BindingRequirementKey = tuple[str, str, str]


def _group_scope(
    groups: tuple[Any, ...],
) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    scope: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for group in groups:
        group_id = _text(_get(group, "id"))
        beats = tuple(
            _text(item)
            for item in (
                _get(group, "beat_ids", ())
                or _get(group, "dramatic_beat_ids", ())
                or ()
            )
            if _text(item)
        )
        nested_shots = _items(_get(group, "shots", ()) or ())
        shot_ids = tuple(
            _text(item) for item in (_get(group, "shot_ids", ()) or ()) if _text(item)
        )
        shot_ids = _append_unique(
            shot_ids,
            (
                _text(_get(item, "id"))
                for item in nested_shots
                if _text(_get(item, "id"))
            ),
        )
        for shot_id in shot_ids:
            prior_groups, prior_beats = scope.get(shot_id, ((), ()))
            scope[shot_id] = (
                _append_unique(prior_groups, (group_id,)),
                _append_unique(prior_beats, beats),
            )
    return scope


def _requirements(
    groups: tuple[Any, ...], shots: tuple[Any, ...]
) -> tuple[_ProjectedRequirement, ...]:
    scope = _group_scope(groups)
    result: list[_ProjectedRequirement] = []
    positions: dict[tuple[str, str, str], int] = {}
    for shot in shots:
        shot_id = _text(_get(shot, "id"))
        group_ids, group_beats = scope.get(shot_id, ((), ()))
        shot_beats = tuple(_get(shot, "dramatic_beat_ids", ()) or ())
        beat_ids = _append_unique(group_beats, shot_beats)
        for source in _get(shot, "asset_requirements", ()) or ():
            source_kind = _text(_get(source, "kind"))
            entity_key = _text(_get(source, "entity_key"))
            if not entity_key:
                continue
            base_entity_id = ""
            variant_id = ""
            malformed = False
            if source_kind in {"character_identity", "character_state"}:
                kind: AssetKind = "character_identity"
            elif source_kind == "scene_base":
                kind = "scene_base"
            elif source_kind == "scene_state":
                kind = "scene_variant"
                base_entity_id, variant_id = structured_scene_requirement(source)
                malformed = not base_entity_id or not variant_id
            elif source_kind == "prop":
                kind = "prop"
            else:
                continue
            requirement = _ProjectedRequirement(
                kind=kind,
                entity_key=entity_key,
                base_entity_id=base_entity_id,
                variant_id=variant_id,
                group_ids=group_ids,
                beat_ids=beat_ids,
                shot_ids=(shot_id,) if shot_id else (),
                required=bool(_get(source, "required", True)),
                malformed_scene_state=malformed,
            )
            position = positions.get(requirement.key)
            if position is None:
                positions[requirement.key] = len(result)
                result.append(requirement)
                continue
            current = result[position]
            result[position] = replace(
                current,
                group_ids=_append_unique(current.group_ids, group_ids),
                beat_ids=_append_unique(current.beat_ids, beat_ids),
                shot_ids=_append_unique(current.shot_ids, (shot_id,)),
                required=current.required or requirement.required,
            )
    return tuple(result)


def required_binding_keys_for_director_group(
    director_plan: Any,
    group_id: str,
) -> frozenset[BindingRequirementKey]:
    """Return required structural reference identities for one active plan group."""
    groups = tuple(
        group
        for group in _items(_get(director_plan, "groups", ()) or ())
        if _text(_get(group, "id")) == group_id
    )
    if len(groups) != 1:
        raise PlannedReferencesRequired(
            f"当前导演方案不存在叙事组 {group_id}，请返回规划处理"
        )
    shots = tuple(
        shot for group in groups for shot in _items(_get(group, "shots", ()) or ())
    )
    return frozenset(
        requirement.key
        for requirement in _requirements(groups, shots)
        if requirement.required
    )


def _binding_requirement_key(
    binding: PlannedReferenceBinding,
) -> BindingRequirementKey:
    if binding.asset_kind == "scene_variant":
        if binding.base_entity_id and binding.variant_id:
            return binding.asset_kind, binding.base_entity_id, binding.variant_id
        return binding.asset_kind, "", binding.entity_id
    return binding.asset_kind, binding.entity_id, ""


def _binding(
    requirement: _ProjectedRequirement,
    *,
    project_id: str,
    episode_number: int,
    source_plan_revision_id: str,
    characters: tuple[Any, ...],
    scenes: tuple[Any, ...],
    props: tuple[Any, ...],
    episode_identity_ids: frozenset[str] = frozenset(),
    identity_default_map: Mapping[str, str] | None = None,
) -> PlannedReferenceBinding:
    status = "missing_asset"
    resolution = "auto_matched"
    entity_id = requirement.entity_key
    base_entity_id = requirement.base_entity_id
    variant_id = requirement.variant_id
    label = requirement.entity_key
    invalid_slot = False

    if requirement.kind == "character_identity":
        candidates, force_pending = _character_identity_candidates(
            requirement.entity_key,
            characters,
            episode_identity_ids,
            identity_default_map or {},
        )
        if len(candidates) == 1 and not force_pending:
            character, identity = candidates[0]
            entity_id = _text(_get(identity, "identity_id"))
            character_name = _text(_get(character, "name")) or _text(
                _get(identity, "character_name")
            )
            identity_name = _text(_get(identity, "identity_name"))
            label = " / ".join(item for item in (character_name, identity_name) if item)
            try:
                slot_id = character_state_slot_id(character_name, entity_id)
            except ValueError:
                slot_id = ""
                status = "pending_confirmation"
                invalid_slot = True
            else:
                status = (
                    "missing_image"
                    if _explicitly_missing_image(identity, identity=True)
                    else "ready"
                )
        elif candidates or force_pending:
            slot_id = ""
            status = "pending_confirmation"
        else:
            slot_id = ""
    elif requirement.kind == "scene_base":
        candidates = [
            scene
            for scene in scenes
            if _text(_get(scene, "name")) == requirement.entity_key
            and not _text(_get(scene, "base_scene_id"))
            and not _text(_get(scene, "variant_id"))
        ]
        if len(candidates) == 1:
            scene = candidates[0]
            entity_id = _text(_get(scene, "name"))
            label = entity_id
            status = "missing_image" if _explicitly_missing_image(scene) else "ready"
        elif len(candidates) > 1:
            status = "pending_confirmation"
        if len(candidates) == 1:
            try:
                slot_id = scene_base_slot_id(entity_id, "master")
            except ValueError:
                slot_id = ""
                status = "pending_confirmation"
                invalid_slot = True
        else:
            slot_id = ""
    elif requirement.kind == "scene_variant" and requirement.malformed_scene_state:
        status = "pending_confirmation"
        slot_id = ""
    elif requirement.kind == "scene_variant":
        candidates = [
            scene
            for scene in scenes
            if _text(_get(scene, "base_scene_id")) == base_entity_id
            and _text(_get(scene, "variant_id")) == variant_id
        ]
        if len(candidates) == 1:
            scene = candidates[0]
            entity_id = _text(_get(scene, "name"))
            label = " / ".join((base_entity_id, variant_id))
            status = "missing_image" if _explicitly_missing_image(scene) else "ready"
        elif len(candidates) > 1:
            status = "pending_confirmation"
        if len(candidates) == 1:
            try:
                slot_id = scene_state_slot_id(base_entity_id, entity_id, "master")
            except ValueError:
                slot_id = ""
                status = "pending_confirmation"
                invalid_slot = True
        else:
            slot_id = ""
    else:
        candidates = [
            prop
            for prop in props
            if _text(_get(prop, "name")) == requirement.entity_key
            or requirement.entity_key
            in {
                _text(alias)
                for alias in (_get(prop, "aliases", ()) or ())
                if _text(alias)
            }
        ]
        if len(candidates) == 1:
            prop = candidates[0]
            entity_id = _text(_get(prop, "name"))
            label = entity_id
            status = "missing_image" if _explicitly_missing_image(prop) else "ready"
        elif len(candidates) > 1:
            status = "pending_confirmation"
        if len(candidates) == 1:
            try:
                slot_id = prop_reference_slot_id(entity_id)
            except ValueError:
                slot_id = ""
                status = "pending_confirmation"
                invalid_slot = True
        else:
            slot_id = ""

    if invalid_slot:
        if not entity_id:
            entity_id = requirement.entity_key
            label = requirement.entity_key
        elif not label:
            label = requirement.entity_key

    return PlannedReferenceBinding.create(
        project_id=project_id,
        episode_number=episode_number,
        source_plan_revision_id=source_plan_revision_id,
        asset_kind=requirement.kind,
        entity_id=entity_id,
        base_entity_id=base_entity_id,
        variant_id=variant_id,
        asset_slot_id=slot_id,
        group_ids=requirement.group_ids,
        beat_ids=requirement.beat_ids,
        shot_ids=requirement.shot_ids,
        required=requirement.required,
        status=status,
        resolution=resolution,
        display_label=label,
    )


def bindings_for_director_plan(
    *,
    project_id: str,
    episode_number: int,
    source_plan_revision_id: str,
    groups: Iterable[Any] | Mapping[Any, Any],
    shots: Iterable[Any] | Mapping[Any, Any],
    characters: Iterable[Any] | Mapping[Any, Any],
    scenes: Iterable[Any] | Mapping[Any, Any],
    props: Iterable[Any] | Mapping[Any, Any],
    episode_identity_ids: Iterable[str] = (),
    identity_default_map: Mapping[str, str] | None = None,
) -> tuple[PlannedReferenceBinding, ...]:
    """Project current DirectorPlan relationships without I/O or mutation."""
    group_items = _items(groups)
    shot_items = _items(shots)
    character_items = _items(characters)
    scene_items = _items(scenes)
    prop_items = _items(props)
    selected_identity_ids = frozenset(
        _text(identity_id) for identity_id in episode_identity_ids if _text(identity_id)
    )
    default_identity_ids = identity_default_map or {}
    return tuple(
        _binding(
            requirement,
            project_id=project_id,
            episode_number=episode_number,
            source_plan_revision_id=source_plan_revision_id,
            characters=character_items,
            scenes=scene_items,
            props=prop_items,
            episode_identity_ids=selected_identity_ids,
            identity_default_map=default_identity_ids,
        )
        for requirement in _requirements(group_items, shot_items)
    )


def bindings_by_kind(
    bindings: Iterable[PlannedReferenceBinding],
) -> dict[AssetKind, tuple[PlannedReferenceBinding, ...]]:
    """Group bindings by kind while retaining their projection order."""
    grouped: dict[AssetKind, list[PlannedReferenceBinding]] = {}
    for binding in bindings:
        grouped.setdefault(binding.asset_kind, []).append(binding)
    return {kind: tuple(values) for kind, values in grouped.items()}


def _asset_path(project_dir: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = project_dir / path
    return path.absolute()


def _unavailable(
    binding: PlannedReferenceBinding,
    warning: str,
    *,
    status: BindingStatus | None = None,
) -> ResolvedPlannedReference:
    return ResolvedPlannedReference(
        binding_id=binding.binding_id,
        asset_kind=binding.asset_kind,
        entity_id=binding.entity_id,
        display_label=binding.display_label,
        variant_id=binding.variant_id,
        beat_ids=binding.beat_ids,
        required=binding.required,
        status=status or binding.status,
        selected_by_default=False,
        asset_slot_id=binding.asset_slot_id,
        warning=warning,
        group_ids=binding.group_ids,
        shot_ids=binding.shot_ids,
    )


def _resolve_binding(
    binding: PlannedReferenceBinding,
    workflow_store: ProductionWorkflowStore,
    project_dir: Path,
) -> ResolvedPlannedReference:
    if binding.status != "ready":
        return _unavailable(binding, f"planned binding status is {binding.status}")
    try:
        slot, versions = workflow_store.get_slot(binding.asset_slot_id)
    except KeyError:
        return _unavailable(binding, "asset slot is unavailable", status="missing_asset")
    version_id = str(slot.current_version_id or "")
    version = versions.get(version_id)
    if version is None or version.slot_id != binding.asset_slot_id:
        return _unavailable(
            binding, "asset slot has no valid current version", status="missing_image"
        )
    adoption_status = version.adoption_status.value
    if adoption_status not in {
        AdoptionStatus.PROVISIONAL.value,
        AdoptionStatus.ADOPTED.value,
    }:
        return _unavailable(
            binding,
            f"current version status is {adoption_status}",
            status="pending_confirmation",
        )
    try:
        validated = validate_reference_image(
            _asset_path(project_dir, version.asset_path),
            allowed_roots=(project_dir / "assets",),
        )
        relative_path = Path(validated.image_path).resolve().relative_to(
            project_dir.resolve()
        ).as_posix()
        sha256 = validated.sha256
    except (InvalidReferenceUpload, OSError, ValueError):
        return _unavailable(
            binding, "current version is not a safe valid image", status="missing_image"
        )
    return ResolvedPlannedReference(
        binding_id=binding.binding_id,
        asset_kind=binding.asset_kind,
        entity_id=binding.entity_id,
        display_label=binding.display_label,
        variant_id=binding.variant_id,
        beat_ids=binding.beat_ids,
        required=binding.required,
        status=binding.status,
        selected_by_default=True,
        asset_slot_id=binding.asset_slot_id,
        version_id=version.version_id,
        adoption_status=adoption_status,
        thumbnail_url=validated.image_path,
        relative_path=relative_path,
        sha256=sha256,
        group_ids=binding.group_ids,
        shot_ids=binding.shot_ids,
    )


def _reference_revision(
    bindings: Sequence[PlannedReferenceBinding],
    resolved: Sequence[ResolvedPlannedReference],
) -> str:
    canonical = json.dumps(
        {
            "bindings": [item.model_dump(mode="json") for item in bindings],
            "versions": [item.model_dump(mode="json") for item in resolved],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def resolve_planned_reference_preview(
    store: PlannedBindingStore,
    workflow_store: ProductionWorkflowStore,
    *,
    project_id: str,
    episode_number: int,
    group_id: str,
    project_dir: Path,
    max_images: int = 9,
    active_plan_revision_id: str | None = None,
    required_binding_keys: frozenset[BindingRequirementKey] | None = None,
) -> PlannedReferencePreview:
    """Resolve only persisted bindings and current versions without mutation."""
    if isinstance(max_images, bool) or not isinstance(max_images, int) or max_images < 1:
        raise InvalidPlannedReference("max_images must be a positive integer")
    bindings = await store.list_planned_reference_bindings(
        episode_number, group_id=group_id
    )
    return _preview_from_bindings(
        bindings,
        workflow_store,
        project_id=project_id,
        episode_number=episode_number,
        group_id=group_id,
        project_dir=project_dir,
        max_images=max_images,
        active_plan_revision_id=active_plan_revision_id,
        required_binding_keys=required_binding_keys,
    )


def _preview_from_bindings(
    bindings: Sequence[PlannedReferenceBinding],
    workflow_store: ProductionWorkflowStore,
    *,
    project_id: str,
    episode_number: int,
    group_id: str,
    project_dir: Path,
    max_images: int,
    active_plan_revision_id: str | None = None,
    required_binding_keys: frozenset[BindingRequirementKey] | None = None,
) -> PlannedReferencePreview:
    if not bindings:
        raise PlannedReferencesRequired(
            "请先重新规划本集身份、场景和道具引用"
        )
    if any(
        item.project_id != project_id
        or item.episode_number != episode_number
        or group_id not in item.group_ids
        for item in bindings
    ):
        raise InvalidPlannedReference("planned binding scope does not match request")
    revisions = {item.source_plan_revision_id for item in bindings}
    if len(revisions) != 1 or (
        active_plan_revision_id is not None
        and revisions != {active_plan_revision_id}
    ):
        raise StaleReferenceBinding("planned references do not match active director plan")
    if required_binding_keys is not None:
        published_required = {
            _binding_requirement_key(item) for item in bindings if item.required
        }
        missing = sorted(required_binding_keys - published_required)
        if missing:
            labels = ", ".join(
                ":".join(part for part in key if part) for key in missing
            )
            raise PlannedReferencesRequired(
                f"当前导演方案仍有未规划的必需引用: {labels}"
            )
    root = Path(project_dir).resolve(strict=False)
    resolved = tuple(_resolve_binding(item, workflow_store, root) for item in bindings)
    return PlannedReferencePreview(
        reference_revision=_reference_revision(bindings, resolved),
        bindings=resolved,
        max_images=max_images,
    )


def _planned_snapshot_image(
    item: ResolvedPlannedReference,
    project_dir: Path,
    *,
    project_id: str,
    episode_number: int,
) -> PlannedSnapshotReferenceImage:
    return PlannedSnapshotReferenceImage(
        requirement_id=item.binding_id,
        source="matched",
        source_id=item.version_id,
        asset_kind=item.asset_kind,
        image_path=str(project_dir / item.relative_path),
        resolution="matched",
        entity_id=item.entity_id,
        shot_ids=item.shot_ids,
        binding_id=item.binding_id,
        asset_slot_id=item.asset_slot_id,
        version_id=item.version_id,
        relative_path=item.relative_path,
        sha256=item.sha256,
        group_ids=item.group_ids,
        beat_ids=item.beat_ids,
        project_id=project_id,
        episode_number=episode_number,
    )


async def build_planned_reference_snapshot(
    store: PlannedBindingStore,
    workflow_store: ProductionWorkflowStore,
    *,
    project_id: str,
    episode_number: int,
    group_id: str,
    project_dir: Path,
    selected_binding_ids: Sequence[str],
    upload_ids: Sequence[str],
    reference_revision: str,
    uploads: Mapping[str, ReferenceUpload] | None = None,
    max_images: int = 9,
    active_plan_revision_id: str | None = None,
    required_binding_keys: frozenset[BindingRequirementKey] | None = None,
) -> ReferenceDecisionSnapshot:
    """Re-resolve and freeze exactly selected bindings and temporary uploads."""
    if len(set(selected_binding_ids)) != len(selected_binding_ids):
        raise InvalidPlannedReference("duplicate selected binding IDs")
    if len(set(upload_ids)) != len(upload_ids):
        raise InvalidPlannedReference("duplicate upload IDs")
    bindings = await store.list_planned_reference_bindings(
        episode_number, group_id=group_id
    )
    with production_workflow_project_lock(workflow_store.state_path.parent):
        current_workflow = ProductionWorkflowStore(workflow_store.state_path)
        preview = _preview_from_bindings(
            bindings,
            current_workflow,
            project_id=project_id,
            episode_number=episode_number,
            group_id=group_id,
            project_dir=project_dir,
            max_images=max_images,
            active_plan_revision_id=active_plan_revision_id,
            required_binding_keys=required_binding_keys,
        )
    if preview.reference_revision != reference_revision:
        raise StaleReferenceBinding("planned reference binding revision changed")
    by_id = {item.binding_id: item for item in preview.bindings}
    unknown = [item for item in selected_binding_ids if item not in by_id]
    if unknown:
        raise InvalidPlannedReference("binding does not belong to requested group")
    unresolved = [
        item.binding_id
        for item in preview.bindings
        if item.required and not item.version_id
    ]
    if unresolved:
        raise UnresolvedPlannedReference(
            "unresolved planned references: " + ", ".join(unresolved)
        )
    omitted_required = [
        item.binding_id
        for item in preview.bindings
        if item.required and item.binding_id not in selected_binding_ids
    ]
    if omitted_required:
        raise PlannedReferencesRequired(
            "required planned references were not selected: "
            + ", ".join(omitted_required)
        )
    chosen = [by_id[item] for item in selected_binding_ids]
    if any(not item.version_id for item in chosen):
        raise UnresolvedPlannedReference("selected planned reference is unresolved")
    if len(chosen) + len(upload_ids) > max_images:
        raise InvalidPlannedReference(
            f"reference snapshot contains {len(chosen) + len(upload_ids)} images; "
            f"limit is {max_images}"
        )
    root = Path(project_dir).resolve(strict=False)
    images = [
        _planned_snapshot_image(
            item,
            root,
            project_id=project_id,
            episode_number=episode_number,
        )
        for item in chosen
    ]
    upload_lookup = uploads or {}
    upload_root = root / ".runtime" / "reference_uploads"
    for upload_id in upload_ids:
        upload = upload_lookup.get(upload_id)
        if upload is None or upload.upload_id != upload_id or not upload.temporary:
            raise InvalidPlannedReference("unknown temporary upload")
        try:
            validated = validate_reference_image(
                upload.image_path,
                allowed_roots=(upload_root,),
                expected_mime=upload.mime_type,
            )
            relative_path = Path(validated.image_path).resolve().relative_to(
                root
            ).as_posix()
        except (InvalidReferenceUpload, OSError, ValueError):
            raise InvalidPlannedReference("temporary upload is not a safe valid image") from None
        images.append(
            PlannedSnapshotReferenceImage(
                requirement_id="",
                source="upload",
                source_id=upload_id,
                asset_kind="additional",
                image_path=validated.image_path,
                resolution="temporary",
                entity_id=upload_id,
                relative_path=relative_path,
                sha256=validated.sha256,
                group_ids=(group_id,),
                project_id=project_id,
                episode_number=episode_number,
            )
        )
    return ReferenceDecisionSnapshot(
        id=f"refsnap_{uuid.uuid4().hex}",
        schema_version="narrative-reference-decision/v2",
        images=tuple(images),
        ignored_requirement_ids=(),
    )


__all__ = [
    "InvalidPlannedReference",
    "PlannedReferencePreview",
    "ReadOnlyPlannedBindingStore",
    "PlannedSnapshotReferenceImage",
    "PlannedReferencesRequired",
    "ResolvedPlannedReference",
    "StaleReferenceBinding",
    "UnresolvedPlannedReference",
    "bindings_by_kind",
    "bindings_for_director_plan",
    "build_planned_reference_snapshot",
    "required_binding_keys_for_director_group",
    "resolve_planned_reference_preview",
]
