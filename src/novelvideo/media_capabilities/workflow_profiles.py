from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from novelvideo.media_capabilities.models import MediaCapability, WorkflowProfile


MAX_SOURCE_BYTES = 5 * 1024 * 1024
MAX_DEPTH = 64
MAX_NODES = 10_000
MAX_BINDINGS = 256
MAX_OUTPUTS = 256
MAX_CONTAINER_ITEMS = 100_000
_SECRET_SEGMENTS = {
    "auth",
    "authorization",
    "credential",
    "credentials",
    "password",
    "passwd",
    "token",
    "secret",
}
_COMPACT_SECRET_KEYS = {
    "apikey",
    "accesstoken",
    "clientsecret",
    "refreshtoken",
    "privatekey",
    "bearertoken",
    "secretkey",
    "apisecret",
    "authkey",
}


class WorkflowImportError(ValueError):
    """Raised when an imported workflow is unsafe or structurally invalid."""


class _Binding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    node_id: str
    field: str

    @field_validator("node_id", "field")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("invalid identifier")
        return value


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    node_id: str
    media_type: str

    @field_validator("node_id", "media_type")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("invalid identifier")
        return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")


def _object_from_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorkflowImportError("workflow_import.duplicate_key")
        result[key] = value
    return result


def _decode_json(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_SOURCE_BYTES:
        raise WorkflowImportError("workflow source exceeds 5 MiB")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkflowImportError("workflow source must be valid UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_from_pairs,
            parse_constant=_reject_constant,
        )
    except WorkflowImportError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise WorkflowImportError("workflow source must be valid JSON") from exc
    if not isinstance(value, dict):
        raise WorkflowImportError("workflow source must be a JSON object")
    return value


def _read_path_source(source: Path, allowed_root: Path | None) -> bytes:
    if allowed_root is None:
        raise WorkflowImportError("workflow_import.path_root_required")
    try:
        if source.is_symlink():
            raise WorkflowImportError("workflow_import.invalid_path")
        root = allowed_root.resolve(strict=True)
        resolved = source.resolve(strict=True)
        resolved.relative_to(root)
        if not resolved.is_file():
            raise WorkflowImportError("workflow_import.invalid_path")
        with resolved.open("rb") as stream:
            return stream.read(MAX_SOURCE_BYTES + 1)
    except WorkflowImportError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise WorkflowImportError("workflow_import.invalid_path") from exc


def _load_source(
    source: Path | bytes | str | Mapping[str, Any], allowed_root: Path | None
) -> dict[str, Any]:
    if isinstance(source, Path):
        return _decode_json(_read_path_source(source, allowed_root))
    if isinstance(source, bytes):
        return _decode_json(source)
    if isinstance(source, str):
        try:
            raw = source.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise WorkflowImportError("workflow source must be valid UTF-8") from exc
        return _decode_json(raw)
    if isinstance(source, Mapping):
        return dict(source)
    raise WorkflowImportError("unsupported workflow source type")


def _key_segments(key: str) -> list[str]:
    key = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", key)
    key = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    return re.findall(r"[a-z0-9]+", key.casefold())


def _is_secret_key(key: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", key.casefold())
    if compact in _COMPACT_SECRET_KEYS:
        return True
    segments = _key_segments(key)
    if _SECRET_SEGMENTS.intersection(segments):
        return True
    return any(
        left in {"api", "private"} and right == "key"
        for left, right in zip(segments, segments[1:])
    )


def _inspect_structure(value: Any) -> None:
    stack: list[tuple[Any, int, bool]] = [(value, 0, False)]
    active: set[int] = set()
    total_items = 0
    try:
        while stack:
            current, depth, exiting = stack.pop()
            is_mapping = isinstance(current, Mapping)
            is_sequence = isinstance(current, Sequence) and not isinstance(
                current, (str, bytes, bytearray)
            )
            if not is_mapping and not is_sequence:
                continue
            identity = id(current)
            if exiting:
                active.remove(identity)
                continue
            if depth > MAX_DEPTH:
                raise WorkflowImportError("workflow_import.structure_limit")
            if identity in active:
                raise WorkflowImportError("workflow_import.invalid_structure")
            active.add(identity)
            stack.append((current, depth, True))
            if is_mapping:
                items = list(current.items())
                total_items += len(items)
                if total_items > MAX_CONTAINER_ITEMS:
                    raise WorkflowImportError("workflow_import.structure_limit")
                for key, child in reversed(items):
                    if not isinstance(key, str):
                        raise WorkflowImportError("workflow_import.invalid_structure")
                    if _is_secret_key(key):
                        raise WorkflowImportError("workflow_import.prohibited_field")
                    stack.append((child, depth + 1, False))
            else:
                total_items += len(current)
                if total_items > MAX_CONTAINER_ITEMS:
                    raise WorkflowImportError("workflow_import.structure_limit")
                for child in reversed(current):
                    stack.append((child, depth + 1, False))
    except WorkflowImportError:
        raise
    except (RecursionError, RuntimeError, TypeError, ValueError) as exc:
        raise WorkflowImportError("workflow_import.invalid_structure") from exc


def _canonicalize(source: Mapping[str, Any]) -> tuple[dict[str, Any], bytes]:
    try:
        canonical = json.dumps(
            source,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError) as exc:
        raise WorkflowImportError("workflow source must contain only JSON values") from exc
    if len(canonical) > MAX_SOURCE_BYTES:
        raise WorkflowImportError("normalized workflow source exceeds 5 MiB")
    return json.loads(canonical), canonical


def _parse_bindings(bindings: Mapping[str, object]) -> dict[str, _Binding]:
    try:
        return {name: _Binding.model_validate(value) for name, value in bindings.items()}
    except (AttributeError, TypeError, ValidationError) as exc:
        raise WorkflowImportError("invalid workflow binding descriptor") from exc


def _parse_outputs(outputs: Mapping[str, object]) -> dict[str, _Output]:
    try:
        return {name: _Output.model_validate(value) for name, value in outputs.items()}
    except (AttributeError, TypeError, ValidationError) as exc:
        raise WorkflowImportError("invalid workflow output descriptor") from exc


def _referenced_node(workflow: Mapping[str, Any], node_id: str) -> dict[str, Any]:
    node = workflow.get(node_id)
    if not isinstance(node, dict):
        raise WorkflowImportError("workflow_import.invalid_reference")
    if not isinstance(node.get("class_type"), str) or not isinstance(
        node.get("inputs"), dict
    ):
        raise WorkflowImportError("workflow_import.invalid_reference")
    return node


def import_profile(
    workflow_id: str,
    source: Path | bytes | str | Mapping[str, Any],
    bindings: Mapping[str, object],
    outputs: Mapping[str, object],
    *,
    allowed_root: Path | None = None,
    id: str | None = None,
    version: int = 1,
    capabilities: list[MediaCapability | str] | None = None,
    constraints: Mapping[str, Any] | None = None,
) -> WorkflowProfile:
    """Validate a workflow export and return an inactive draft profile."""

    chosen_id = workflow_id if id is None else id
    if (
        not workflow_id
        or workflow_id != workflow_id.strip()
        or not chosen_id
        or chosen_id != chosen_id.strip()
    ):
        raise WorkflowImportError("workflow_import.invalid_identifier")
    if len(bindings) > MAX_BINDINGS or len(outputs) > MAX_OUTPUTS:
        raise WorkflowImportError("workflow_import.structure_limit")

    loaded_source = _load_source(source, allowed_root)
    _inspect_structure(loaded_source)
    _inspect_structure(bindings)
    _inspect_structure(outputs)
    _inspect_structure(constraints or {})
    if len(loaded_source) > MAX_NODES:
        raise WorkflowImportError("workflow_import.structure_limit")
    workflow, canonical = _canonicalize(loaded_source)
    parsed_bindings = _parse_bindings(bindings)
    parsed_outputs = _parse_outputs(outputs)
    final_bindings = {
        name: value.model_dump() for name, value in parsed_bindings.items()
    }
    final_outputs = {name: value.model_dump() for name, value in parsed_outputs.items()}
    _inspect_structure(final_bindings)
    _inspect_structure(final_outputs)

    for binding in parsed_bindings.values():
        node = _referenced_node(workflow, binding.node_id)
        if binding.field not in node["inputs"]:
            raise WorkflowImportError("workflow_import.invalid_reference")
    for output in parsed_outputs.values():
        _referenced_node(workflow, output.node_id)

    try:
        return WorkflowProfile(
            id=chosen_id,
            version=version,
            workflow_id=workflow_id,
            capabilities=capabilities or [],
            bindings=final_bindings,
            outputs=final_outputs,
            constraints=dict(constraints or {}),
            source_sha256=hashlib.sha256(canonical).hexdigest(),
            status="draft",
        )
    except ValidationError as exc:
        raise WorkflowImportError("invalid workflow profile metadata") from exc
