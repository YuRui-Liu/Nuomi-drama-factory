"""Secret-free contracts; stores must copy versions at persistence boundaries."""
from __future__ import annotations

from copy import deepcopy
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from novelvideo.text_task_runtime.models import AgentTaskRoute

Identifier = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
Revision = Annotated[int, Field(strict=True, ge=1)]
PreferenceKey = Literal["pace", "camera_motion", "composition", "performance", "method"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ResourceRef(Contract):
    id: Identifier
    revision: Revision


class ResourceVersion(ResourceRef):
    kind: Literal["prompt", "skill", "reference"]
    owner: Identifier
    content: str = Field(strict=True)
    content_hash: Identifier
    archived: bool = Field(default=False, strict=True)


class MethodConfig(Contract):
    model: Literal["project"] | AgentTaskRoute = "project"
    prompt: str = Field(default="", strict=True)
    skills: tuple[ResourceRef, ...] = ()
    references: tuple[ResourceRef, ...] = ()
    director_preferences: dict[PreferenceKey, str] = Field(default_factory=dict)


class RoleDefinition(Contract):
    id: Identifier
    name: Identifier
    subtasks: tuple[Identifier, ...] = Field(min_length=1)
    adapter_id: Identifier | None = None
    connected: bool = Field(default=False, strict=True)


class TeamVersion(Contract):
    id: Identifier
    revision: Revision
    name: Identifier
    roles: dict[str, dict[str, MethodConfig]] = Field(default_factory=dict)


def validate_override_fields(value: dict[str, Any]) -> dict[str, Any]:
    """Validate a partial method without turning missing fields into overrides."""
    if not isinstance(value, dict):
        raise ValueError("method overrides must be an object")
    MethodConfig.model_validate(value)
    return deepcopy(value)


class ProjectDraft(Contract):
    project_id: Identifier
    template_id: Identifier
    template_revision: Revision
    draft_revision: int = Field(default=0, strict=True, ge=0)
    active_revision: int = Field(default=0, strict=True, ge=0)
    overrides: dict[str, dict[str, dict[str, Any]]] = Field(default_factory=dict)

    @field_validator("overrides")
    @classmethod
    def validate_overrides(cls, value):
        return {role: {subtask: validate_override_fields(fields)
                       for subtask, fields in subtasks.items()}
                for role, subtasks in value.items()}


class ExecutionSnapshot(Contract):
    id: Identifier
    project_id: Identifier
    template_id: Identifier
    template_revision: Revision
    active_revision: Revision
    role_id: Identifier
    subtask_id: Identifier
    input_revision: Identifier
    input_hash: Identifier
    resolved_method: MethodConfig
    resolved_model: AgentTaskRoute
    resource_snapshots: tuple[ResourceVersion, ...] = ()
