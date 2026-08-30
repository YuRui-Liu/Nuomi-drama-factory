"""Secret-free contracts for routing background text tasks."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TextTaskRuntimeName = Literal["codex", "model_api"]
TextTaskFallback = Literal["stop"]
TextTaskRouteSource = Literal["global", "project", "task"]
TextTaskReasoningEffort = Literal[
    "none", "minimal", "low", "medium", "high", "xhigh"
]
TEXT_TASK_MODEL_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"


def validate_text_task_model_name(value: str) -> str:
    """Reject values that cannot safely cross Windows command launchers."""

    if re.fullmatch(TEXT_TASK_MODEL_PATTERN, value) is None:
        raise ValueError("model must be a safe provider model identifier")
    return value


class AgentTaskRoute(BaseModel):
    """A complete route selected for a structured background task."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    runtime: TextTaskRuntimeName = "model_api"
    model: str = Field(default="deepseek-v4-flash", pattern=TEXT_TASK_MODEL_PATTERN)
    reasoning_effort: TextTaskReasoningEffort | None = None
    skill_id: str | None = None
    skill_version: str | None = None
    fallback: TextTaskFallback = "stop"


class AgentTaskRouteOverride(BaseModel):
    """Optional fields layered over a global route."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    runtime: TextTaskRuntimeName | None = None
    model: str | None = Field(default=None, pattern=TEXT_TASK_MODEL_PATTERN)
    reasoning_effort: TextTaskReasoningEffort | None = None
    skill_id: str | None = None
    skill_version: str | None = None
    fallback: TextTaskFallback | None = None


class AgentTaskRoutingConfig(BaseModel):
    """Versioned mapping from logical task roles to route overrides."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    routes: dict[str, AgentTaskRouteOverride] = Field(default_factory=dict)


class AgentTaskRouteSnapshot(AgentTaskRoute):
    """Resolved immutable route stored with a queued task."""

    task_role: str = Field(min_length=1)
    source: TextTaskRouteSource
