from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExecutionStatus(str, Enum):
    DRAFT = "draft"
    PREFLIGHT_FAILED = "preflight_failed"
    QUEUED = "queued"
    RUNNING = "running"
    PROVIDER_PROCESSING = "provider_processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class AdoptionStatus(str, Enum):
    CANDIDATE = "candidate"
    PROVISIONAL = "provisional"
    ADOPTED = "adopted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class AssetOrigin(str, Enum):
    GENERATED = "generated"
    UPLOADED = "uploaded"
    LEGACY_IMPORT = "legacy_import"


class GenerationRouteSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    workflow_revision: str = Field(min_length=1)
    bindings: dict[str, Any] = Field(default_factory=dict)
    aspect_ratio: str = Field(min_length=1)
    duration: float = Field(gt=0)
    mode: str = Field(min_length=1)


class GenerationAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: str = Field(min_length=1)
    slot_id: str = Field(min_length=1)
    status: ExecutionStatus = ExecutionStatus.DRAFT
    input_snapshot: dict[str, Any] = Field(default_factory=dict)
    routing_snapshot: dict[str, Any] = Field(default_factory=dict)
    resolved_route: GenerationRouteSnapshot | None = None
    reference_summary: dict[str, Any] = Field(default_factory=dict)
    provider_task_id: str | None = None
    cost: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | str | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AssetVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str = Field(min_length=1)
    slot_id: str = Field(min_length=1)
    source_attempt_id: str | None = None
    asset_path: str = Field(min_length=1)
    origin: AssetOrigin = AssetOrigin.GENERATED
    generation_metadata: dict[str, Any] | None = None
    adoption_status: AdoptionStatus = AdoptionStatus.CANDIDATE
    qc_passed: bool = False
    soft_issues: list[str] = Field(default_factory=list)
    technical_error: str | None = None
    created_at: datetime | None = None


class AssetSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_id: str = Field(min_length=1)
    asset_kind: str = Field(min_length=1)
    critical: bool = False
    current_version_id: str | None = None
    version_ids: list[str] = Field(default_factory=list)


class ProjectProductionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aspect_ratio: str = "9:16"
    strict_mode: bool = False
    production_recipe_version: str = ""
    default_routes: dict[str, str] = Field(default_factory=dict)
    concurrency_limits: dict[str, int] = Field(default_factory=dict)
    auto_provisional: bool = True


class AdoptionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_id: str
    version_id: str
    from_status: AdoptionStatus
    to_status: AdoptionStatus
    actor: str
    at: datetime
    reason: str = ""
    source_attempt_id: str | None = None
