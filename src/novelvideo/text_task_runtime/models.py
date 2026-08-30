"""Secret-free contracts for routing background text tasks."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TextTaskRuntimeName = Literal["codex", "model_api"]
TextTaskFallback = Literal["stop", "retry", "explicit_backup"]
TextTaskRouteSource = Literal["global", "project", "task"]


class AgentTaskRoute(BaseModel):
    """A complete route selected for a structured background task."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    runtime: TextTaskRuntimeName = "model_api"
    model: str = Field(default="deepseek-v4-flash", min_length=1)
    reasoning_effort: str | None = None
    skill: str | None = None
    fallback: TextTaskFallback = "stop"
    backup_runtime: TextTaskRuntimeName | None = None
    backup_model: str | None = None


class AgentTaskRouteOverride(BaseModel):
    """Optional fields layered over a global route."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    runtime: TextTaskRuntimeName | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    skill: str | None = None
    fallback: TextTaskFallback | None = None
    backup_runtime: TextTaskRuntimeName | None = None
    backup_model: str | None = None


class AgentTaskRoutingConfig(BaseModel):
    """Versioned mapping from logical task roles to route overrides."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    routes: dict[str, AgentTaskRouteOverride] = Field(default_factory=dict)


class AgentTaskRouteSnapshot(AgentTaskRoute):
    """Resolved immutable route stored with a queued task."""

    task_role: str = Field(min_length=1)
    source: TextTaskRouteSource

