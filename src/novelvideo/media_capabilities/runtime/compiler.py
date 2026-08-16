"""Compile semantic workflow inputs into RunningHub node information."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import TypedDict

from pydantic import JsonValue

from novelvideo.media_capabilities.models import WorkflowProfile


class CompiledNodeInfo(TypedDict):
    nodeId: str
    fieldName: str
    fieldValue: JsonValue


class WorkflowCompileError(ValueError):
    """A workflow could not be compiled according to its public contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _is_finite_number(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        or isinstance(value, float)
        and math.isfinite(value)
    )


def _validated_bindings(profile: WorkflowProfile) -> dict[str, tuple[str, str]]:
    validated: dict[str, tuple[str, str]] = {}
    for semantic_name, descriptor in profile.bindings.items():
        if not isinstance(descriptor, Mapping) or set(descriptor) != {
            "node_id",
            "field",
        }:
            raise WorkflowCompileError("workflow.invalid_binding_descriptor")
        node_id = descriptor["node_id"]
        field_name = descriptor["field"]
        if (
            not isinstance(node_id, str)
            or not node_id
            or node_id != node_id.strip()
            or not isinstance(field_name, str)
            or not field_name
            or field_name != field_name.strip()
        ):
            raise WorkflowCompileError("workflow.invalid_binding_descriptor")
        validated[semantic_name] = (node_id, field_name)
    return validated


def _validate_constraints(
    constraints: Mapping[str, JsonValue], values: Mapping[str, JsonValue]
) -> None:
    required: list[str] = []
    if "required" in constraints:
        raw_required = constraints["required"]
        if not isinstance(raw_required, list) or not all(
            isinstance(name, str) for name in raw_required
        ):
            raise WorkflowCompileError("workflow.invalid_constraint_descriptor")
        required = raw_required

    properties: Mapping[str, JsonValue] = {}
    if "properties" in constraints:
        raw_properties = constraints["properties"]
        if not isinstance(raw_properties, Mapping):
            raise WorkflowCompileError("workflow.invalid_constraint_descriptor")
        for name, rules in raw_properties.items():
            if not isinstance(name, str) or not isinstance(rules, Mapping):
                raise WorkflowCompileError("workflow.invalid_constraint_descriptor")
            if "enum" in rules and not isinstance(rules["enum"], list):
                raise WorkflowCompileError("workflow.invalid_constraint_descriptor")
            for bound in ("minimum", "maximum"):
                if bound in rules and not _is_finite_number(rules[bound]):
                    raise WorkflowCompileError(
                        "workflow.invalid_constraint_descriptor"
                    )
            if (
                "minimum" in rules
                and "maximum" in rules
                and rules["minimum"] > rules["maximum"]
            ):
                raise WorkflowCompileError("workflow.invalid_constraint_descriptor")
        properties = raw_properties

    mutually_exclusive: list[list[str]] = []
    if "mutually_exclusive" in constraints:
        raw_mutually_exclusive = constraints["mutually_exclusive"]
        if not isinstance(raw_mutually_exclusive, list) or not all(
            isinstance(group, list)
            and all(isinstance(name, str) for name in group)
            for group in raw_mutually_exclusive
        ):
            raise WorkflowCompileError("workflow.invalid_constraint_descriptor")
        mutually_exclusive = raw_mutually_exclusive

    if any(name not in values for name in required):
        raise WorkflowCompileError("workflow.required_semantic_input")

    for name, rules in properties.items():
        if name not in values:
            continue
        value = values[name]
        enum = rules.get("enum")
        if isinstance(enum, list) and value not in enum:
            raise WorkflowCompileError("workflow.enum_constraint")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            minimum = rules.get("minimum")
            if isinstance(minimum, (int, float)) and value < minimum:
                raise WorkflowCompileError("workflow.minimum_constraint")
            maximum = rules.get("maximum")
            if isinstance(maximum, (int, float)) and value > maximum:
                raise WorkflowCompileError("workflow.maximum_constraint")

    for group in mutually_exclusive:
        if sum(name in values for name in group) > 1:
            raise WorkflowCompileError("workflow.mutually_exclusive")


def _natural_node_key(node_id: str) -> tuple[tuple[int, int | str], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in re.split(r"(\d+)", node_id)
        if part
    )


def compile_node_info(
    profile: WorkflowProfile, semantic_values: Mapping[str, JsonValue]
) -> list[CompiledNodeInfo]:
    """Validate and deterministically compile allowlisted semantic inputs."""

    bindings = _validated_bindings(profile)
    if set(semantic_values) - set(bindings):
        raise WorkflowCompileError("workflow.unknown_semantic_input")
    _validate_constraints(profile.constraints, semantic_values)

    compiled = [
        (
            semantic_name,
            CompiledNodeInfo(
                nodeId=bindings[semantic_name][0],
                fieldName=bindings[semantic_name][1],
                fieldValue=value,
            ),
        )
        for semantic_name, value in semantic_values.items()
    ]
    compiled.sort(
        key=lambda item: (
            _natural_node_key(item[1]["nodeId"]),
            item[1]["fieldName"],
            item[0],
        )
    )
    return [item for _, item in compiled]
