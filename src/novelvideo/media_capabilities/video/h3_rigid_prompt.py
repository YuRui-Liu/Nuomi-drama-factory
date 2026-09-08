"""Typed fields for the H3 rigid fifteen-section prompt protocol."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast, overload

from pydantic import BaseModel, ConfigDict, Field, model_validator


_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")
_RESERVED_WIRE_MARKERS = ("<d>", "</d>", "<scenetrans>", "<cutoff>")
_RESERVED_WIRE_FIELDS = (
    "integrated_multimodal_description:",
    "overall_soundscape:",
    "non_diegetic_music:",
)
H3_RIGID_SECTION_ORDER = (
    "SCENE CONTEXT",
    "ACTIVE REFERENCES",
    "LOCATION MAP",
    "FIRST FRAME AND SPATIAL BLOCKING",
    "FORMAT MODE",
    "OPTICS",
    "CAMERA",
    "ACTION TIMING",
    "PHYSICS",
    "LIGHTING",
    "AUDIO",
    "CHARACTER ACTING",
    "STYLE",
    "QUALITY",
    "POSITIVE CONSTRAINTS",
)
_RESERVED_SECTION_HEADINGS = frozenset(
    heading.casefold() for heading in H3_RIGID_SECTION_ORDER
)
_T = TypeVar("_T", bound=BaseModel)


class _H3RigidModel(BaseModel):
    model_config = _MODEL_CONFIG

    @model_validator(mode="before")
    @classmethod
    def reject_wire_control_characters(cls, value: object) -> object:
        def validate(item: object) -> None:
            if isinstance(item, str):
                if not item.strip():
                    raise ValueError("rigid prompt text must not be blank")
                if any(
                    unicodedata.category(char) in {"Cc", "Zl", "Zp"}
                    for char in item
                ):
                    raise ValueError(
                        "rigid prompt text must not contain a control character"
                    )
                lowered = item.casefold()
                if any(marker in lowered for marker in _RESERVED_WIRE_MARKERS):
                    raise ValueError("rigid prompt text contains a reserved wire marker")
                if any(field in lowered for field in _RESERVED_WIRE_FIELDS):
                    raise ValueError("rigid prompt text contains a reserved wire field")
                if item.strip().casefold() in _RESERVED_SECTION_HEADINGS:
                    raise ValueError("rigid prompt text must not equal a section heading")
            elif isinstance(item, Mapping):
                for nested in item.values():
                    validate(nested)
            elif isinstance(item, (tuple, list, set, frozenset)):
                for nested in item:
                    validate(nested)

        validate(value)
        return value


class H3SceneContextPlan(_H3RigidModel):
    exact_character_count: int = Field(ge=0)
    active_characters: tuple[str, ...]
    summary: str = Field(min_length=1)


class H3ActiveReference(_H3RigidModel):
    tag: str = Field(pattern=r"^@[A-Za-z0-9][A-Za-z0-9_.-]*$")
    kind: Literal["character", "location"]
    role: str = Field(min_length=1)
    inherit: tuple[str, ...]
    exclude: tuple[str, ...]


class H3LocationMapPlan(_H3RigidModel):
    geography: str = Field(min_length=1)
    landmarks: tuple[str, ...]
    camera_side: str = Field(min_length=1)
    axis: str = Field(min_length=1)


class H3SubjectBlocking(_H3RigidModel):
    character_id: str = Field(min_length=1)
    position: str = Field(min_length=1)
    facing: str = Field(min_length=1)
    gaze: str = Field(min_length=1)
    held_props: tuple[str, ...] = ()


class H3SpatialBlockingPlan(_H3RigidModel):
    shot_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    subjects: tuple[H3SubjectBlocking, ...]


class H3FormatPlan(_H3RigidModel):
    mode: Literal["single_take", "hard_cuts"]
    total_duration_seconds: float = Field(gt=0)
    real_time: bool
    speed_ramps: tuple[str, ...]
    cut_points_seconds: tuple[float, ...]

    @model_validator(mode="after")
    def validate_cut_points(self) -> "H3FormatPlan":
        if self.mode == "single_take" and self.cut_points_seconds:
            raise ValueError("single_take format must not contain cut points")
        previous = 0.0
        for cut_point in self.cut_points_seconds:
            if cut_point <= 0 or cut_point >= self.total_duration_seconds:
                raise ValueError("cut points must be inside total duration")
            if cut_point <= previous:
                raise ValueError("cut points must be strictly increasing")
            previous = cut_point
        return self


class H3OpticsPlan(_H3RigidModel):
    shot_id: str = Field(min_length=1)
    lens_or_fov: str = Field(min_length=1)
    camera_height: str = Field(min_length=1)
    subject_distance: str = Field(min_length=1)
    depth_of_field: str = Field(min_length=1)
    focus_plan: str = Field(min_length=1)


class H3PhysicsPlan(_H3RigidModel):
    statements: tuple[str, ...]


class H3LightingPlan(_H3RigidModel):
    source_logic: str = Field(min_length=1)
    primary_source: str = Field(min_length=1)
    origin: str = Field(min_length=1)
    direction: str = Field(min_length=1)
    shadow_direction: str = Field(min_length=1)
    quality: str = Field(min_length=1)
    color: str = Field(min_length=1)
    subject_effect: str = Field(min_length=1)
    environment_effect: str = Field(min_length=1)
    fill_logic: str = Field(min_length=1)
    catchlight: str = Field(min_length=1)
    contact_shadows: str = Field(min_length=1)
    continuity_key: str = Field(min_length=1)


class H3CharacterActingPlan(_H3RigidModel):
    character_id: str = Field(min_length=1)
    state: str = Field(min_length=1)
    want: str = Field(min_length=1)
    hidden: str = Field(min_length=1)
    body_rhythm: str = Field(min_length=1)
    visible_behavior: str = Field(min_length=1)
    change: str = Field(min_length=1)


class H3QualityPlan(_H3RigidModel):
    requirements: tuple[str, ...]


class H3PositiveConstraint(_H3RigidModel):
    assertion: str = Field(min_length=1)
    count: int | None = Field(default=None, ge=0)


class H3RigidPromptPlan(_H3RigidModel):
    scene_context: H3SceneContextPlan
    active_references: tuple[H3ActiveReference, ...]
    location_map: H3LocationMapPlan
    spatial_blocking: tuple[H3SpatialBlockingPlan, ...]
    format_mode: H3FormatPlan
    optics: tuple[H3OpticsPlan, ...]
    physics: H3PhysicsPlan
    lighting: H3LightingPlan
    character_acting: tuple[H3CharacterActingPlan, ...]
    style_prefix: str = Field(min_length=1)
    quality: H3QualityPlan
    positive_constraints: tuple[H3PositiveConstraint, ...]


def _is_empty(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (Mapping, tuple, list, set, frozenset)):
        return not value
    return False


@overload
def fill_empty_fields(existing: _T, fallback: _T) -> _T: ...


@overload
def fill_empty_fields(
    existing: Mapping[str, Any], fallback: Mapping[str, Any]
) -> dict[str, Any]: ...


def fill_empty_fields(
    existing: BaseModel | Mapping[str, Any],
    fallback: BaseModel | Mapping[str, Any],
) -> BaseModel | dict[str, Any]:
    """Fill only wholly empty fields, preserving all authored non-empty values."""
    if isinstance(existing, BaseModel):
        if not isinstance(fallback, type(existing)):
            raise TypeError("fallback must have the same model type as existing")
        return cast(BaseModel, _fill_value(existing, fallback))
    if not isinstance(fallback, Mapping):
        raise TypeError("fallback must be a mapping when existing is a mapping")
    return cast(dict[str, Any], _fill_value(existing, fallback))


def _fill_value(existing: object, fallback: object) -> object:
    if _is_empty(existing):
        return fallback
    if isinstance(existing, BaseModel) and isinstance(fallback, type(existing)):
        merged_payload = {
            name: _fill_value(getattr(existing, name), getattr(fallback, name))
            for name in type(existing).model_fields
        }
        return type(existing).model_validate(merged_payload)
    if isinstance(existing, Mapping) and isinstance(fallback, Mapping):
        result = dict(existing)
        for name, value in fallback.items():
            result[name] = (
                _fill_value(result[name], value) if name in result else value
            )
        return result
    if isinstance(existing, (list, tuple)) and isinstance(fallback, (list, tuple)):
        if len(existing) != len(fallback):
            return existing
        merged = tuple(
            _fill_value(current, default)
            for current, default in zip(existing, fallback, strict=True)
        )
        return merged if isinstance(existing, tuple) else list(merged)
    return existing


__all__ = [
    "H3_RIGID_SECTION_ORDER",
    "H3ActiveReference",
    "H3CharacterActingPlan",
    "H3FormatPlan",
    "H3LightingPlan",
    "H3LocationMapPlan",
    "H3OpticsPlan",
    "H3PhysicsPlan",
    "H3PositiveConstraint",
    "H3QualityPlan",
    "H3RigidPromptPlan",
    "H3SceneContextPlan",
    "H3SpatialBlockingPlan",
    "H3SubjectBlocking",
    "fill_empty_fields",
]
