"""Pure projection of DirectorPlan asset requirements into stable bindings."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from novelvideo.production_workflow.slot_ids import (
    character_state_slot_id,
    prop_reference_slot_id,
    scene_base_slot_id,
    scene_state_slot_id,
)

from .planned_bindings import AssetKind, PlannedReferenceBinding
from .reference_requirements import structured_scene_requirement


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


def _explicitly_missing_image(entity: Any, *, identity: bool = False) -> bool:
    if _get(entity, "has_reference_image", None) is False:
        return True
    if _text(_get(entity, "image_status", "")) in {"missing", "missing_image"}:
        return True
    if identity:
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


def _binding(
    requirement: _ProjectedRequirement,
    *,
    project_id: str,
    episode_number: int,
    source_plan_revision_id: str,
    characters: tuple[Any, ...],
    scenes: tuple[Any, ...],
    props: tuple[Any, ...],
) -> PlannedReferenceBinding:
    status = "missing_asset"
    resolution = "auto_matched"
    entity_id = requirement.entity_key
    base_entity_id = requirement.base_entity_id
    variant_id = requirement.variant_id
    label = requirement.entity_key

    if requirement.kind == "character_identity":
        candidates = [
            (character, identity)
            for character in characters
            for identity in _identities(character)
            if _text(_get(identity, "identity_id")) == requirement.entity_key
        ]
        if len(candidates) == 1:
            character, identity = candidates[0]
            entity_id = _text(_get(identity, "identity_id"))
            character_name = _text(_get(character, "name")) or _text(
                _get(identity, "character_name")
            )
            identity_name = _text(_get(identity, "identity_name"))
            label = " / ".join(item for item in (character_name, identity_name) if item)
            slot_id = character_state_slot_id(character_name, entity_id)
            status = (
                "missing_image"
                if _explicitly_missing_image(identity, identity=True)
                else "ready"
            )
        elif len(candidates) > 1:
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
        slot_id = (
            scene_base_slot_id(entity_id, "master") if len(candidates) == 1 else ""
        )
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
        slot_id = (
            scene_state_slot_id(base_entity_id, entity_id, "master")
            if len(candidates) == 1
            else ""
        )
    else:
        candidates = [
            prop
            for prop in props
            if _text(_get(prop, "name")) == requirement.entity_key
        ]
        if len(candidates) == 1:
            prop = candidates[0]
            entity_id = _text(_get(prop, "name"))
            label = entity_id
            status = "missing_image" if _explicitly_missing_image(prop) else "ready"
        elif len(candidates) > 1:
            status = "pending_confirmation"
        slot_id = prop_reference_slot_id(entity_id) if len(candidates) == 1 else ""

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
) -> tuple[PlannedReferenceBinding, ...]:
    """Project current DirectorPlan relationships without I/O or mutation."""
    group_items = _items(groups)
    shot_items = _items(shots)
    character_items = _items(characters)
    scene_items = _items(scenes)
    prop_items = _items(props)
    return tuple(
        _binding(
            requirement,
            project_id=project_id,
            episode_number=episode_number,
            source_plan_revision_id=source_plan_revision_id,
            characters=character_items,
            scenes=scene_items,
            props=prop_items,
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


__all__ = ["bindings_by_kind", "bindings_for_director_plan"]
