"""Secret-free contracts for routing background text tasks."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TextTaskRuntimeName = Literal["codex", "model_api", "workbuddy", "deepseek_harness"]
TextTaskFallback = Literal["stop"]
TextTaskRouteSource = Literal["global", "project", "task"]
# "max" is only honoured by the WorkBuddy CLI (--effort max); the model_api
# runtime normalises it away because OpenAI-compatible endpoints reject it.
TextTaskReasoningEffort = Literal[
    "none", "minimal", "low", "medium", "high", "xhigh", "max"
]
# 模型 ID 允许字母数字与 . _ : / - 以及空格，匹配 CLI 常见的"厂商 模型"格式（如
# "Hy4 preview"）。禁止首尾空白、长度上限 128。
TEXT_TASK_MODEL_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/\- ]{0,127}$"


def validate_text_task_model_name(value: str) -> str:
    """Trim and reject values that cannot safely cross Windows command launchers."""

    stripped = value.strip()
    if not stripped:
        raise ValueError("model must not be blank")
    if re.fullmatch(TEXT_TASK_MODEL_PATTERN, stripped) is None:
        raise ValueError("model must be a safe provider model identifier")
    return stripped


class AgentTaskRoute(BaseModel):
    """A complete route selected for a structured background task."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    runtime: TextTaskRuntimeName = "model_api"
    model: str = Field(default="deepseek-v4-flash")
    reasoning_effort: TextTaskReasoningEffort | None = None
    skill_id: str | None = None
    skill_version: str | None = None
    fallback: TextTaskFallback = "stop"

    @field_validator("model")
    @classmethod
    def _validate_model(cls, value: str) -> str:
        return validate_text_task_model_name(value)


class AgentTaskRouteOverride(BaseModel):
    """Optional fields layered over a global route."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    runtime: TextTaskRuntimeName | None = None
    model: str | None = None
    reasoning_effort: TextTaskReasoningEffort | None = None
    skill_id: str | None = None
    skill_version: str | None = None
    fallback: TextTaskFallback | None = None

    @field_validator("model")
    @classmethod
    def _validate_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_text_task_model_name(value)


class RuntimePreset(BaseModel):
    """按运行时记忆的模型与推理强度。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str
    reasoning_effort: TextTaskReasoningEffort | None = None

    @field_validator("model")
    @classmethod
    def _validate_model(cls, value: str) -> str:
        return validate_text_task_model_name(value)


class AgentTaskRoutingConfig(BaseModel):
    """Versioned mapping from logical task roles to route overrides."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    routes: dict[str, AgentTaskRouteOverride] = Field(default_factory=dict)
    runtime_presets: dict[TextTaskRuntimeName, RuntimePreset] = Field(default_factory=dict)


class AgentTaskRouteSnapshot(AgentTaskRoute):
    """Resolved immutable route stored with a queued task."""

    task_role: str = Field(min_length=1)
    source: TextTaskRouteSource
