"""Strict semantic input validation and deterministic nodeInfoList compilation."""

from __future__ import annotations
import json
import math
import re
from .graph import validate_bindings
from .models import FileReference, InputDefinition, WorkflowError, WorkflowManifest


def validate_value(rule: InputDefinition, value: object, name: str) -> object:
    if value is None:
        if rule.nullable:
            return None
        raise WorkflowError("null_input", name)
    valid = {
        "string": isinstance(value, str),
        "integer": type(value) is int,
        "number": type(value) in (int, float),
        "boolean": type(value) is bool,
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "file": isinstance(value, dict),
        "files": isinstance(value, list),
    }
    if not valid[rule.type]:
        raise WorkflowError("input_type", name)
    if rule.type in {"integer", "number"}:
        if (
            not math.isfinite(value)
            or (rule.minimum is not None and value < rule.minimum)
            or (rule.maximum is not None and value > rule.maximum)
        ):
            raise WorkflowError("input_range", name)
    if rule.enum is not None and not any(
        type(value) is type(v) and value == v for v in rule.enum
    ):
        raise WorkflowError("input_enum", name)
    if isinstance(value, (str, list, dict)):
        if (rule.min_length is not None and len(value) < rule.min_length) or (
            rule.max_length is not None and len(value) > rule.max_length
        ):
            raise WorkflowError("input_length", name)
    if rule.pattern is not None and (
        not isinstance(value, str) or re.fullmatch(rule.pattern, value) is None
    ):
        raise WorkflowError("input_pattern", name)
    if rule.format == "uint64" and (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdigit()
        or len(value) > 20
        or int(value) > 2**64 - 1
    ):
        raise WorkflowError("uint64", name)
    if rule.type == "object" and rule.properties:
        value = normalize_inputs(
            rule.properties, value, allow_extra=rule.additional_properties
        )
    if rule.type == "array" and rule.items:
        value = [
            validate_value(rule.items, item, f"{name}[{i}]")
            for i, item in enumerate(value)
        ]
    if rule.type in {"file", "files"}:
        values = [value] if rule.type == "file" else value
        try:
            parsed = [FileReference.model_validate(v).model_dump() for v in values]
        except ValueError as exc:
            raise WorkflowError("file_reference", name) from exc
        return parsed[0] if rule.type == "file" else parsed
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise WorkflowError("invalid_json_value", name) from exc
    return value


def normalize_inputs(
    rules: dict[str, InputDefinition], values: dict, *, allow_extra: bool = False
) -> dict:
    if not isinstance(values, dict):
        raise WorkflowError("input_type")
    if not allow_extra and set(values) - set(rules):
        raise WorkflowError(
            "unknown_input", ", ".join(sorted(set(values) - set(rules)))
        )
    result = {k: v for k, v in values.items() if k not in rules} if allow_extra else {}
    for name, rule in rules.items():
        if name in values:
            value = values[name]
        elif "default" in rule.model_fields_set:
            value = rule.default
        elif rule.required:
            raise WorkflowError("required", name)
        else:
            continue
        result[name] = validate_value(rule, value, name)
    return result


def compile_bindings(
    manifest: WorkflowManifest, graph: dict, values: dict
) -> list[dict]:
    validate_bindings(manifest, graph)
    result = []
    for binding in manifest.bindings:
        if binding.source not in values:
            continue
        for target in binding.targets:
            value = values[binding.source]
            if target.encoding == "json":
                value = json.dumps(
                    value,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            elif target.encoding == "string":
                if value is None or isinstance(value, (dict, list)):
                    raise WorkflowError("scalar_encoding", binding.source)
                value = str(value).lower() if isinstance(value, bool) else str(value)
            result.append(
                {
                    "nodeId": target.node_id,
                    "fieldName": target.field,
                    "fieldValue": value,
                }
            )
    return sorted(
        result,
        key=lambda v: (
            [
                (0, int(p)) if p.isdigit() else (1, p)
                for p in re.split(r"(\d+)", v["nodeId"])
            ],
            v["fieldName"],
        ),
    )


def compile_inputs(manifest: WorkflowManifest, graph: dict, values: dict) -> list[dict]:
    normalized = normalize_inputs(manifest.inputs, values)
    if any(manifest.inputs[k].type in {"file", "files"} for k in normalized):
        raise WorkflowError("files_require_runtime")
    return compile_bindings(manifest, graph, normalized)
