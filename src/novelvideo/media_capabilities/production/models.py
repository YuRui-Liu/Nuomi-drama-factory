"""Persistent production DAG records and lifecycle states."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, JsonValue, StringConstraints


Identifier = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]


class ProductionRunStatus(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProductionNodeStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    QUALITY_FAILED = "quality_failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class _ProductionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProductionRun(_ProductionRecord):
    id: Identifier
    project_id: Identifier
    status: ProductionRunStatus
    config_snapshot: JsonValue
    created_at: datetime
    updated_at: datetime


class ProductionNode(_ProductionRecord):
    id: Identifier
    run_id: Identifier
    node_type: Identifier
    idempotency_key: Identifier
    status: ProductionNodeStatus
    config_snapshot: JsonValue
    created_at: datetime
    updated_at: datetime


class ProductionEdge(_ProductionRecord):
    id: Identifier
    run_id: Identifier
    upstream_node_id: Identifier
    downstream_node_id: Identifier
    created_at: datetime
