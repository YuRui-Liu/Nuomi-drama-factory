"""Public parameter schema for configurable video workflows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, BeforeValidator, ConfigDict, model_validator
from typing_extensions import Self


def _normalize_non_empty_string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("value must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("value must not be empty")
    return normalized


NormalizedString = Annotated[str, BeforeValidator(_normalize_non_empty_string)]


class VideoWorkflowParameterOption(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: NormalizedString
    label: NormalizedString
    description: str = ""
    relative_cost: Literal["standard", "higher"] = "standard"


class VideoWorkflowParameterDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: NormalizedString
    type: Literal["enum"] = "enum"
    label: NormalizedString
    description: str = ""
    default: NormalizedString
    scope: Literal["narrative_group"] = "narrative_group"
    options: tuple[VideoWorkflowParameterOption, ...]

    @model_validator(mode="after")
    def validate_options(self) -> Self:
        if not self.options:
            raise ValueError("parameter options must not be empty")
        values: set[str] = set()
        for option in self.options:
            if option.value in values:
                raise ValueError(
                    f"duplicate parameter option value: {option.value}"
                )
            values.add(option.value)
        if self.default not in values:
            raise ValueError("parameter default must be an option")
        return self


class VideoWorkflowParameterError(ValueError):
    """Workflow parameter overrides do not satisfy the public schema."""


class _ParameterizedWorkflow(Protocol):
    parameters: tuple[VideoWorkflowParameterDefinition, ...]


def resolve_workflow_parameters(
    definition: _ParameterizedWorkflow,
    overrides: Mapping[str, str],
) -> dict[str, str]:
    """Return defaults merged with validated workflow parameter overrides."""
    parameters = {parameter.key: parameter for parameter in definition.parameters}
    resolved = {
        parameter.key: parameter.default for parameter in definition.parameters
    }
    for key, value in overrides.items():
        parameter = parameters.get(key)
        if parameter is None:
            raise VideoWorkflowParameterError(
                f"unknown workflow parameter: {key}"
            )
        allowed_values = {option.value for option in parameter.options}
        if value not in allowed_values:
            raise VideoWorkflowParameterError(
                f"invalid value for workflow parameter {key}: {value}"
            )
        resolved[key] = value
    return resolved


__all__ = [
    "VideoWorkflowParameterDefinition",
    "VideoWorkflowParameterError",
    "VideoWorkflowParameterOption",
    "resolve_workflow_parameters",
]
