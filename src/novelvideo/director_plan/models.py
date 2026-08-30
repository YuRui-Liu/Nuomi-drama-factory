from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from math import isfinite
from typing import Annotated, Literal, NoReturn, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_serializer,
    field_validator,
    model_validator,
)
from ulid import ULID


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceSpan(FrozenModel):
    id: str
    ordinal: int = Field(gt=0)
    scene: str
    time: str
    text: str
    dialogue_text: str = ""


class ValidationIssue(FrozenModel):
    code: str
    message: str
    location: str
    severity: Literal["error", "warning"] = "error"


class ValidationReport(FrozenModel):
    passed: bool = False
    issues: tuple[ValidationIssue, ...] = ()
    version: int = 1


def _freeze_json_value(value: JsonValue) -> object:
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("JSON floats must be finite")
    if isinstance(value, Mapping):
        return _FrozenMapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(item) for item in value)
    return value


def _thaw_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    return value


class _FrozenMapping(dict[str, object]):
    def __init__(self, value: Mapping[str, JsonValue]) -> None:
        if getattr(self, "_initialized", False):
            raise TypeError("frozen mapping cannot be reinitialized")
        dict.__init__(self)
        for key, item in value.items():
            dict.__setitem__(self, key, _freeze_json_value(item))
        object.__setattr__(self, "_initialized", True)

    def __deepcopy__(self, memo: dict[int, object]) -> Self:
        memo[id(self)] = self
        return self

    @staticmethod
    def _deny_mutation(*args: object, **kwargs: object) -> NoReturn:
        raise TypeError("frozen mapping cannot be modified")

    __setitem__ = _deny_mutation
    __delitem__ = _deny_mutation
    clear = _deny_mutation
    pop = _deny_mutation
    popitem = _deny_mutation
    setdefault = _deny_mutation
    update = _deny_mutation
    __ior__ = _deny_mutation
    __setattr__ = _deny_mutation
    __delattr__ = _deny_mutation


class AssetMigrationReport(FrozenModel):
    items: tuple[dict[str, JsonValue], ...] = ()

    @field_validator("items", mode="after")
    @classmethod
    def freeze_items(
        cls, items: tuple[dict[str, JsonValue], ...]
    ) -> tuple[dict[str, JsonValue], ...]:
        return tuple(_FrozenMapping(item) for item in items)

    @field_serializer("items")
    def serialize_items(self, items: tuple[dict[str, JsonValue], ...]) -> object:
        return _thaw_json_value(items)


class StyleProjections(FrozenModel):
    director: str
    image: str
    video: str
    panel_tag: str


class StyleSnapshot(FrozenModel):
    snapshot_id: str
    style_id: str
    style_version: str
    catalog_hash: str
    style_hash: str
    projections: StyleProjections


class GenerationBatchPlan(FrozenModel):
    id: str
    group_id: str
    shot_ids: tuple[str, ...] = Field(min_length=1, max_length=4)
    layout: Literal["single", "diptych", "triptych", "grid_2x2"]
    rows: int = Field(gt=0)
    columns: int = Field(gt=0)
    capacity: int = Field(gt=0, le=4)
    style_snapshot_id: str
    style_snapshot_hash: str = Field(min_length=1)
    model: str = Field(default="gpt-image-2", min_length=1)
    aspect_ratio: str = Field(default="9:16", pattern=r"^[1-9]\d*:[1-9]\d*$")
    resolution: Literal["1K", "2K", "4K"] = "2K"
    reference_image_limit: int = Field(default=10, ge=0, le=14)
    retry_limit: int = Field(default=2, ge=0, le=10)

    @field_validator(
        "id",
        "group_id",
        "style_snapshot_id",
        "style_snapshot_hash",
        "model",
        mode="before",
    )
    @classmethod
    def normalize_required_strings(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("value must not be blank")
        return value

    @model_validator(mode="after")
    def validate_layout(self) -> Self:
        expected = {
            "single": (1, 1, 1),
            "diptych": (1, 2, 2),
            "triptych": (1, 3, 3),
            "grid_2x2": (2, 2, 4),
        }[self.layout]
        if (self.rows, self.columns, self.capacity) != expected:
            raise ValueError("batch layout dimensions and capacity must agree")
        if len(self.shot_ids) != self.capacity:
            raise ValueError("batch capacity must equal shot count")
        if len(set(self.shot_ids)) != len(self.shot_ids):
            raise ValueError("batch shot ids must be unique")
        if any(not shot_id.strip() for shot_id in self.shot_ids):
            raise ValueError("batch shot ids must not be blank")
        return self


class VideoSegmentPlan(FrozenModel):
    id: str
    group_id: str
    shot_ids: tuple[str, ...] = Field(min_length=1)
    duration_seconds: float = Field(gt=0, le=15)
    continuity_reason: str
    audio_mode: Literal["project_default", "external_tts", "h3_original"]
    style_snapshot_id: str
    style_snapshot_hash: str = Field(min_length=1)

    @field_validator(
        "id",
        "group_id",
        "continuity_reason",
        "style_snapshot_id",
        "style_snapshot_hash",
        mode="before",
    )
    @classmethod
    def normalize_required_strings(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("value must not be blank")
        return value

    @model_validator(mode="after")
    def validate_shot_ids(self) -> Self:
        if len(set(self.shot_ids)) != len(self.shot_ids):
            raise ValueError("segment shot ids must be unique")
        if any(not shot_id.strip() for shot_id in self.shot_ids):
            raise ValueError("segment shot ids must not be blank")
        return self


def canonical_production_plan_hash(value: Mapping[str, object]) -> str:
    """Hash the canonical JSON payload, excluding its self-referential hash."""
    payload = dict(value)
    payload.pop("production_plan_hash", None)
    payload.setdefault("model", "gpt-image-2")
    payload.setdefault("aspect_ratio", "9:16")
    payload.setdefault("resolution", "2K")
    payload.setdefault("reference_image_limit", 10)
    payload.setdefault("retry_limit", 2)
    payload.setdefault("generation_batches", ())
    payload.setdefault("video_segments", ())
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_canonical_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_json_default(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


class ProductionPlan(FrozenModel):
    revision_id: str
    episode: int = Field(gt=0)
    style_snapshot_hash: str = Field(min_length=1)
    model: str = Field(default="gpt-image-2", min_length=1)
    aspect_ratio: str = Field(default="9:16", pattern=r"^[1-9]\d*:[1-9]\d*$")
    resolution: Literal["1K", "2K", "4K"] = "2K"
    reference_image_limit: int = Field(default=10, ge=0, le=14)
    retry_limit: int = Field(default=2, ge=0, le=10)
    generation_batches: tuple[GenerationBatchPlan, ...] = ()
    video_segments: tuple[VideoSegmentPlan, ...] = ()
    production_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator(
        "revision_id", "style_snapshot_hash", "model", mode="before"
    )
    @classmethod
    def normalize_required_strings(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("value must not be blank")
        return value

    @model_validator(mode="after")
    def validate_consistency_and_hash(self) -> Self:
        batches = self.generation_batches
        segments = self.video_segments
        all_ids = [item.id for item in (*batches, *segments)]
        if len(set(all_ids)) != len(all_ids):
            raise ValueError("production ids must be unique")

        for batch in batches:
            if batch.style_snapshot_hash != self.style_snapshot_hash:
                raise ValueError("batch style snapshot hash must match plan")
            if (
                batch.model,
                batch.aspect_ratio,
                batch.resolution,
                batch.reference_image_limit,
                batch.retry_limit,
            ) != (
                self.model,
                self.aspect_ratio,
                self.resolution,
                self.reference_image_limit,
                self.retry_limit,
            ):
                raise ValueError("batch generation settings must match plan")
        if any(
            segment.style_snapshot_hash != self.style_snapshot_hash
            for segment in segments
        ):
            raise ValueError("segment style snapshot hash must match plan")

        batch_shots = {
            shot_id: batch.group_id
            for batch in batches
            for shot_id in batch.shot_ids
        }
        segment_shots = {
            shot_id: segment.group_id
            for segment in segments
            for shot_id in segment.shot_ids
        }
        batch_count = sum(len(batch.shot_ids) for batch in batches)
        segment_count = sum(len(segment.shot_ids) for segment in segments)
        if len(batch_shots) != batch_count or len(segment_shots) != segment_count:
            raise ValueError("shot ids must be unique within each production collection")
        if batch_shots != segment_shots:
            raise ValueError("batch and segment shot coverage must match")

        canonical = canonical_production_plan_hash(self.model_dump(mode="json"))
        if self.production_plan_hash != canonical:
            raise ValueError("production plan hash must match canonical payload")
        return self


class ShotPlan(FrozenModel):
    id: str
    source_span_ids: tuple[str, ...]
    subject: str
    action: str
    space_anchor: str = ""
    continuous_with_next: bool = False
    visible_start_state: str
    visible_end_state: str
    shot_size: str = "medium"
    camera_angle: str = "eye_level"
    composition: str = ""
    camera_motion: str = "static"
    dialogue_source_ids: tuple[str, ...] = ()
    duration_seconds: float = Field(gt=0, le=15)


class NarrativeGroupPlan(FrozenModel):
    id: str
    ordinal: int = Field(gt=0)
    source_span_ids: tuple[str, ...]
    scene_anchor: str
    time_anchor: str
    objective: str
    visible_turn: str
    relation_to_previous: Literal[
        "single", "causal", "progressive", "contrast", "montage", "time_jump"
    ]
    shots: tuple[ShotPlan, ...] = Field(min_length=1, max_length=5)
    style_snapshot_id: str | None = None


class SplitGroup(FrozenModel):
    kind: Literal["split_group"] = "split_group"
    group_id: str
    before_shot_id: str


class MergeAdjacentGroups(FrozenModel):
    kind: Literal["merge_adjacent_groups"] = "merge_adjacent_groups"
    left_group_id: str
    right_group_id: str


class MoveShot(FrozenModel):
    kind: Literal["move_shot"] = "move_shot"
    shot_id: str
    target_group_id: str
    index: int = Field(ge=0)


class ReorderGroups(FrozenModel):
    kind: Literal["reorder_groups"] = "reorder_groups"
    group_ids: tuple[str, ...]


class UpdateShot(FrozenModel):
    kind: Literal["update_shot"] = "update_shot"
    shot_id: str
    source_span_ids: tuple[str, ...] | None = None
    subject: str | None = None
    action: str | None = None
    visible_start_state: str | None = None
    visible_end_state: str | None = None
    shot_size: str | None = None
    camera_angle: str | None = None
    composition: str | None = None
    camera_motion: str | None = None
    dialogue_source_ids: tuple[str, ...] | None = None
    duration_seconds: float | None = Field(default=None, gt=0, le=15)


DirectorEdit = Annotated[
    SplitGroup | MergeAdjacentGroups | MoveShot | ReorderGroups | UpdateShot,
    Field(discriminator="kind"),
]


class DirectorPlanRevision(FrozenModel):
    revision_id: str
    parent_revision_id: str | None = None
    episode: int = Field(gt=0)
    status: Literal[
        "draft",
        "validating",
        "review_required",
        "active",
        "superseded",
        "abandoned",
        "failed",
    ]
    source_script_hash: str
    director_model: str
    prompt_version: str
    project_style_snapshot_id: str
    project_style_snapshot: StyleSnapshot | None = None
    edit_source: Literal["planner", "human"] = "planner"
    groups: tuple[NarrativeGroupPlan, ...]
    validation_report: ValidationReport = ValidationReport()
    migration_report: AssetMigrationReport = AssetMigrationReport()
    created_at: AwareDatetime
    activated_at: AwareDatetime | None = None

    @field_validator("created_at", "activated_at", mode="after")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.astimezone(timezone.utc)

    @classmethod
    def new(
        cls,
        *,
        episode: int,
        source_script_hash: str,
        director_model: str,
        prompt_version: str,
        project_style_snapshot_id: str,
        project_style_snapshot: StyleSnapshot | None = None,
        groups: tuple[NarrativeGroupPlan, ...],
        parent_revision_id: str | None = None,
    ) -> Self:
        return cls(
            revision_id=str(ULID()),
            parent_revision_id=parent_revision_id,
            episode=episode,
            status="draft",
            source_script_hash=source_script_hash,
            director_model=director_model,
            prompt_version=prompt_version,
            project_style_snapshot_id=project_style_snapshot_id,
            project_style_snapshot=project_style_snapshot,
            groups=groups,
            created_at=datetime.now(timezone.utc),
        )
