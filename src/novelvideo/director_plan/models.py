from __future__ import annotations

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


class GenerationBatch(FrozenModel):
    id: str
    group_id: str
    shot_ids: tuple[str, ...] = Field(min_length=1, max_length=4)
    layout: Literal["single", "diptych", "triptych", "grid_2x2"]
    rows: int = Field(gt=0)
    columns: int = Field(gt=0)
    capacity: int = Field(gt=0, le=4)
    style_snapshot_id: str


class VideoSegment(FrozenModel):
    id: str
    group_id: str
    shot_ids: tuple[str, ...] = Field(min_length=1)
    duration_seconds: float = Field(gt=0, le=15)
    continuity_reason: str
    audio_mode: Literal["project_default", "external_tts", "h3_original"]
    style_snapshot_id: str


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
