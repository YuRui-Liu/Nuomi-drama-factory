from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field
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


class AssetMigrationReport(FrozenModel):
    items: tuple[dict[str, object], ...] = ()


class ShotPlan(FrozenModel):
    id: str
    source_span_ids: tuple[str, ...]
    subject: str
    action: str
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
    groups: tuple[NarrativeGroupPlan, ...]
    validation_report: ValidationReport = ValidationReport()
    migration_report: AssetMigrationReport = AssetMigrationReport()
    created_at: datetime
    activated_at: datetime | None = None

    @classmethod
    def new(
        cls,
        *,
        episode: int,
        source_script_hash: str,
        director_model: str,
        prompt_version: str,
        project_style_snapshot_id: str,
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
            groups=groups,
            created_at=datetime.now(timezone.utc),
        )
