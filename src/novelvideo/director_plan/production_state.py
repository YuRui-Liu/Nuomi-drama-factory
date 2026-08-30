from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Self

from pydantic import AwareDatetime, field_validator

from .models import FrozenModel


ProductionStage = Literal[
    "pending",
    "queued",
    "requested",
    "polling",
    "persisted",
    "quality_checked",
    "failed",
]


class ProductionArtifactResult(FrozenModel):
    uri: str
    sha256: str | None = None
    mime_type: str | None = None


class ProductionError(FrozenModel):
    code: str
    message: str
    retryable: bool = False


class QualityIssue(FrozenModel):
    code: str
    message: str


class ProductionQualityReport(FrozenModel):
    passed: bool
    issues: tuple[QualityIssue, ...] = ()


class ProductionCleanupReport(FrozenModel):
    source_uri: str
    output_uris: tuple[str, ...] = ()
    passed: bool = False
    message: str = ""


class _ProductionItemState(FrozenModel):
    production_id: str
    provider: str | None = None
    request_id: str | None = None
    job_id: str | None = None
    stage: ProductionStage = "pending"
    heartbeat_at: AwareDatetime | None = None
    result: ProductionArtifactResult | None = None
    error: ProductionError | None = None
    quality_report: ProductionQualityReport | None = None

    @field_validator("heartbeat_at", mode="after")
    @classmethod
    def normalize_heartbeat(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.astimezone(timezone.utc)


class GenerationBatchState(_ProductionItemState):
    cleanup_report: ProductionCleanupReport | None = None


class VideoSegmentState(_ProductionItemState):
    pass


class ProductionExecutionState(FrozenModel):
    schema_version: Literal[1] = 1
    revision_id: str
    production_plan_hash: str
    generation_batches: tuple[GenerationBatchState, ...] = ()
    video_segments: tuple[VideoSegmentState, ...] = ()

    @classmethod
    def new(
        cls,
        *,
        revision_id: str,
        production_plan_hash: str,
        generation_batch_ids: tuple[str, ...] = (),
        video_segment_ids: tuple[str, ...] = (),
    ) -> Self:
        return cls(
            revision_id=revision_id,
            production_plan_hash=production_plan_hash,
            generation_batches=tuple(
                GenerationBatchState(production_id=item_id)
                for item_id in generation_batch_ids
            ),
            video_segments=tuple(
                VideoSegmentState(production_id=item_id)
                for item_id in video_segment_ids
            ),
        )
