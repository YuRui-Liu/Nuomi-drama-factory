"""Typed reference requirements projected from DirectorPlan shots."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Literal, Mapping

ReferenceKind = Literal[
    "character_identity", "scene_base", "scene_variant", "prop"
]
ReferenceStatus = Literal[
    "matched",
    "fallback",
    "draft_variant",
    "missing_asset",
    "missing_image",
    "temporary",
    "ignored",
    "invalid",
]


@dataclass(frozen=True)
class ReferenceRequirement:
    id: str
    kind: ReferenceKind
    entity_id: str
    base_entity_id: str = ""
    variant_id: str = ""
    shot_ids: tuple[str, ...] = ()
    required: bool = True


def parse_scene_requirement(value: str, known: set[str]) -> tuple[str, str]:
    """Split a derived scene at the longest known base-name boundary."""
    scene_id = str(value or "").strip()
    known_ids = {str(item or "").strip() for item in known}
    if scene_id in known_ids:
        return scene_id, ""
    bases = [
        candidate
        for candidate in known_ids
        if candidate and scene_id.startswith(f"{candidate}_")
    ]
    if not bases:
        return scene_id, ""
    base = max(bases, key=len)
    return base, scene_id[len(base) + 1 :]


def _get(value: Any, name: str, default: Any = "") -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def reference_requirements_for_shots(
    shots: Iterable[Any], *, known_scene_ids: set[str] | None = None
) -> tuple[ReferenceRequirement, ...]:
    """Aggregate equal references while retaining first-seen ordering."""
    known_scenes = known_scene_ids or set()
    requirements: list[ReferenceRequirement] = []
    positions: dict[tuple[str, str, str, str], int] = {}
    for shot in shots:
        shot_id = str(_get(shot, "id") or "").strip()
        for source in _get(shot, "asset_requirements", ()) or ():
            source_kind = str(_get(source, "kind") or "").strip()
            entity_id = str(_get(source, "entity_key") or "").strip()
            if not entity_id:
                continue
            base_entity_id = ""
            variant_id = ""
            if source_kind in {"character_identity", "character_state"}:
                kind: ReferenceKind = "character_identity"
                if source_kind == "character_state":
                    variant_id = str(_get(source, "visible_change") or "").strip()
            elif source_kind in {"scene_base", "scene_state"}:
                base_entity_id, parsed_variant = parse_scene_requirement(
                    entity_id, known_scenes
                )
                variant_id = parsed_variant
                if source_kind == "scene_state" and not variant_id:
                    variant_id = str(_get(source, "visible_change") or "").strip()
                kind = "scene_variant" if variant_id else "scene_base"
                entity_id = (
                    f"{base_entity_id}_{variant_id}"
                    if variant_id
                    else base_entity_id
                )
            elif source_kind == "prop":
                kind = "prop"
            else:
                continue
            required = bool(_get(source, "required", True))
            key = (
                (kind, base_entity_id, variant_id, "")
                if kind in {"scene_base", "scene_variant"}
                else (kind, entity_id, base_entity_id, variant_id)
            )
            position = positions.get(key)
            if position is None:
                identifier = (
                    f"{kind}:{base_entity_id}:{variant_id}"
                    if kind == "scene_variant"
                    else f"{kind}:{entity_id}"
                )
                positions[key] = len(requirements)
                requirements.append(
                    ReferenceRequirement(
                        id=identifier,
                        kind=kind,
                        entity_id=entity_id,
                        base_entity_id=base_entity_id,
                        variant_id=variant_id,
                        shot_ids=(shot_id,) if shot_id else (),
                        required=required,
                    )
                )
            else:
                current = requirements[position]
                shot_ids = current.shot_ids
                if shot_id and shot_id not in shot_ids:
                    shot_ids = (*shot_ids, shot_id)
                requirements[position] = replace(
                    current,
                    shot_ids=shot_ids,
                    required=current.required or required,
                )
    return tuple(requirements)


__all__ = [
    "ReferenceKind",
    "ReferenceRequirement",
    "ReferenceStatus",
    "parse_scene_requirement",
    "reference_requirements_for_shots",
]
