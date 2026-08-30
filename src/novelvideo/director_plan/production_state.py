from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Self

from pydantic import AwareDatetime, Field, field_validator, model_validator

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
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    mime_type: str | None = None

    @field_validator("uri", "mime_type", mode="before")
    @classmethod
    def normalize_strings(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("value must not be blank")
        return value


class ProductionError(FrozenModel):
    code: str
    message: str
    retryable: bool = False

    @field_validator("code", "message", mode="before")
    @classmethod
    def normalize_strings(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("value must not be blank")
        return value


class QualityIssue(FrozenModel):
    code: str
    message: str

    @field_validator("code", "message", mode="before")
    @classmethod
    def normalize_strings(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("value must not be blank")
        return value


class ProductionQualityReport(FrozenModel):
    passed: bool
    issues: tuple[QualityIssue, ...] = ()


class ProductionCleanupReport(FrozenModel):
    source_uri: str
    output_uris: tuple[str, ...] = ()
    passed: bool = False
    message: str = ""

    @field_validator("source_uri", mode="before")
    @classmethod
    def normalize_source_uri(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("source uri must not be blank")
        return value

    @field_validator("output_uris", mode="after")
    @classmethod
    def validate_output_uris(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(uri.strip() for uri in value)
        if any(not uri for uri in normalized):
            raise ValueError("output uris must not be blank")
        return normalized


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

    @field_validator(
        "production_id",
        "provider",
        "request_id",
        "job_id",
        mode="before",
    )
    @classmethod
    def normalize_strings(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("value must not be blank")
        return value

    @field_validator("heartbeat_at", mode="after")
    @classmethod
    def normalize_heartbeat(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_stage_payload(self) -> Self:
        if self.stage in {"pending", "queued"}:
            if any((self.result, self.error, self.quality_report)):
                raise ValueError("pending or queued items cannot have outcomes")
        elif self.stage == "requested":
            if not self.provider or not self.request_id:
                raise ValueError("requested items require provider and request id")
            if self.result or self.error or self.quality_report:
                raise ValueError("requested items cannot have outcomes")
        elif self.stage == "polling":
            if not self.provider or not self.request_id or not self.job_id:
                raise ValueError(
                    "polling items require provider, request id, and job id"
                )
            if self.result or self.error or self.quality_report:
                raise ValueError("polling items cannot have outcomes")
        elif self.stage == "persisted":
            if self.result is None:
                raise ValueError("persisted items require a result")
            if self.error or self.quality_report:
                raise ValueError("persisted items cannot have error or quality report")
        elif self.stage == "quality_checked":
            if self.result is None or self.quality_report is None:
                raise ValueError(
                    "quality checked items require result and quality report"
                )
            if self.error:
                raise ValueError("quality checked items cannot have an error")
        elif self.stage == "failed" and self.error is None:
            raise ValueError("failed items require an error")
        return self


class GenerationBatchState(_ProductionItemState):
    cleanup_report: ProductionCleanupReport | None = None

    @model_validator(mode="after")
    def validate_cleanup_stage(self) -> Self:
        if self.cleanup_report is not None and (
            self.stage not in {"persisted", "quality_checked", "failed"}
            or self.result is None
        ):
            raise ValueError("cleanup report requires a result-bearing stage")
        return self


class VideoSegmentState(_ProductionItemState):
    pass


class ProductionExecutionState(FrozenModel):
    schema_version: Literal[1] = 1
    state_version: int = Field(default=0, ge=0)
    revision_id: str
    production_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    generation_batches: tuple[GenerationBatchState, ...] = ()
    video_segments: tuple[VideoSegmentState, ...] = ()

    @field_validator("revision_id", mode="before")
    @classmethod
    def normalize_revision_id(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("revision id must not be blank")
        return value

    @model_validator(mode="after")
    def validate_unique_production_ids(self) -> Self:
        ids = [
            item.production_id
            for item in (*self.generation_batches, *self.video_segments)
        ]
        if len(set(ids)) != len(ids):
            raise ValueError("production ids must be unique")
        return self

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
