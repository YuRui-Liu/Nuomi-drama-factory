"""Project-bound knowledge pipeline state and explicit transition rules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from novelvideo.project_config import (
    ProjectConfigReadError,
    load_project_config_file_from_state_dir_strict,
    update_project_config_file_in_state_dir,
)

KNOWLEDGE_PIPELINE_KEY = "knowledge_pipeline"
KNOWLEDGE_PIPELINE_STATUS_KEY = "knowledge_pipeline_status"
KNOWLEDGE_PIPELINE_ERROR_KEY = "knowledge_pipeline_error"
KNOWLEDGE_PIPELINE_RUN_IDENTITY_KEY = "knowledge_pipeline_run_identity"

COGNEE_LEGACY = "cognee_legacy"
KNOWLEDGE_PIPELINE_STRUCTURED = "structured_v1"

STATUS_LEGACY_READY = "legacy_ready"
STATUS_STRUCTURED_PENDING = "structured_pending"
STATUS_STRUCTURED_RUNNING = "structured_running"
STATUS_STRUCTURED_READY = "structured_ready"
STATUS_STRUCTURED_FAILED = "structured_failed"

STRUCTURED_STATUSES = frozenset(
    {
        STATUS_STRUCTURED_PENDING,
        STATUS_STRUCTURED_RUNNING,
        STATUS_STRUCTURED_READY,
        STATUS_STRUCTURED_FAILED,
    }
)
RUN_IDENTITY_KEYS = ("source_sha256", "schema_version", "pipeline_version")

_ALLOWED_TRANSITIONS = {
    STATUS_STRUCTURED_PENDING: frozenset({STATUS_STRUCTURED_RUNNING}),
    STATUS_STRUCTURED_RUNNING: frozenset(
        {STATUS_STRUCTURED_READY, STATUS_STRUCTURED_FAILED}
    ),
    STATUS_STRUCTURED_FAILED: frozenset({STATUS_STRUCTURED_RUNNING}),
    STATUS_STRUCTURED_READY: frozenset({STATUS_STRUCTURED_RUNNING}),
}


class KnowledgePipelineError(RuntimeError):
    """Base error for persisted pipeline state operations."""


class KnowledgePipelineConfigurationError(KnowledgePipelineError):
    """Raised for an explicit but unsupported persisted pipeline value."""


class KnowledgePipelineTransitionError(KnowledgePipelineError):
    """Raised when a structured state transition is not legal."""


class KnowledgePipelineLocked(KnowledgePipelineError):
    """Raised when an explicit switch to legacy is no longer safe."""

    def __init__(self, message: str, *, formal_asset_count: int | None) -> None:
        super().__init__(message)
        self.formal_asset_count = formal_asset_count


class KnowledgePipelineUnsupported(KnowledgePipelineError):
    """Raised when the selected track does not provide a requested capability."""

    error_code = "KNOWLEDGE_PIPELINE_UNSUPPORTED"


@dataclass(frozen=True)
class KnowledgePipelineState:
    pipeline: str
    status: str
    error: str | None = None
    run_identity: dict[str, str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "knowledge_pipeline": self.pipeline,
            "knowledge_pipeline_status": self.status,
            "knowledge_pipeline_error": self.error,
            "knowledge_pipeline_run_identity": self.run_identity,
        }


def _normalize_run_identity(value: object) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    normalized = {key: str(value.get(key) or "").strip() for key in RUN_IDENTITY_KEYS}
    return normalized if all(normalized.values()) else None


def _state_from_config(config: Mapping[str, Any]) -> KnowledgePipelineState:
    raw_pipeline = str(config.get(KNOWLEDGE_PIPELINE_KEY) or "").strip()
    if not raw_pipeline:
        return KnowledgePipelineState(COGNEE_LEGACY, STATUS_LEGACY_READY)
    if raw_pipeline == COGNEE_LEGACY:
        return KnowledgePipelineState(COGNEE_LEGACY, STATUS_LEGACY_READY)
    if raw_pipeline != KNOWLEDGE_PIPELINE_STRUCTURED:
        raise KnowledgePipelineConfigurationError(
            f"Unsupported knowledge pipeline: {raw_pipeline}"
        )

    raw_status = str(config.get(KNOWLEDGE_PIPELINE_STATUS_KEY) or "").strip()
    status = raw_status or STATUS_STRUCTURED_PENDING
    if status not in STRUCTURED_STATUSES:
        raise KnowledgePipelineConfigurationError(
            f"Unsupported structured pipeline status: {status}"
        )
    error = str(config.get(KNOWLEDGE_PIPELINE_ERROR_KEY) or "").strip() or None
    identity = _normalize_run_identity(config.get(KNOWLEDGE_PIPELINE_RUN_IDENTITY_KEY))
    return KnowledgePipelineState(raw_pipeline, status, error, identity)


def knowledge_pipeline_state_from_state_dir(
    state_dir: str | Path | None,
) -> KnowledgePipelineState:
    """Read raw persisted state without adding defaults or writing migration data."""

    if not state_dir:
        return KnowledgePipelineState(COGNEE_LEGACY, STATUS_LEGACY_READY)
    try:
        config = load_project_config_file_from_state_dir_strict(state_dir)
    except ProjectConfigReadError as exc:
        raise KnowledgePipelineConfigurationError(str(exc)) from exc
    return _state_from_config(config)


def knowledge_pipeline_from_state_dir(state_dir: str | Path | None) -> str:
    return knowledge_pipeline_state_from_state_dir(state_dir).pipeline


def is_structured_pipeline(state_dir: str | Path | None) -> bool:
    return (
        knowledge_pipeline_from_state_dir(state_dir)
        == KNOWLEDGE_PIPELINE_STRUCTURED
    )


def is_cognee_legacy(state_dir: str | Path | None) -> bool:
    return knowledge_pipeline_from_state_dir(state_dir) == COGNEE_LEGACY


def transition_structured_pipeline(
    state_dir: str | Path,
    next_status: str,
    *,
    expected_status: str | None = None,
    error: str | None = None,
    run_identity: Mapping[str, object] | None = None,
) -> KnowledgePipelineState:
    """Atomically apply one legal structured pipeline state transition."""

    if next_status not in STRUCTURED_STATUSES:
        raise KnowledgePipelineTransitionError(f"Invalid next status: {next_status}")
    normalized_identity = (
        _normalize_run_identity(run_identity) if run_identity is not None else None
    )
    if run_identity is not None and normalized_identity is None:
        raise KnowledgePipelineTransitionError(
            "run_identity requires source_sha256, schema_version, and pipeline_version"
        )

    def _apply(config: dict) -> None:
        current = _state_from_config(config)
        if current.pipeline != KNOWLEDGE_PIPELINE_STRUCTURED:
            raise KnowledgePipelineTransitionError(
                "Legacy projects cannot enter the structured state machine"
            )
        if expected_status is not None and current.status != expected_status:
            raise KnowledgePipelineTransitionError(
                f"Expected {expected_status}, found {current.status}"
            )
        if current.status == STATUS_STRUCTURED_READY and (
            normalized_identity is None
            or normalized_identity == current.run_identity
        ):
            raise KnowledgePipelineTransitionError(
                "A ready pipeline requires a different run_identity for a new run"
            )
        if next_status not in _ALLOWED_TRANSITIONS[current.status]:
            raise KnowledgePipelineTransitionError(
                f"Illegal transition: {current.status} -> {next_status}"
            )
        config[KNOWLEDGE_PIPELINE_STATUS_KEY] = next_status
        if next_status == STATUS_STRUCTURED_FAILED and str(error or "").strip():
            config[KNOWLEDGE_PIPELINE_ERROR_KEY] = str(error).strip()
        else:
            config.pop(KNOWLEDGE_PIPELINE_ERROR_KEY, None)
        if normalized_identity is not None:
            config[KNOWLEDGE_PIPELINE_RUN_IDENTITY_KEY] = normalized_identity

    try:
        updated = update_project_config_file_in_state_dir(
            state_dir, _apply, strict=True
        )
    except ProjectConfigReadError as exc:
        raise KnowledgePipelineConfigurationError(str(exc)) from exc
    return _state_from_config(updated)


def switch_to_cognee_legacy(
    state_dir: str | Path,
    *,
    formal_asset_count: int | None,
) -> KnowledgePipelineState:
    """Atomically perform the only supported explicit track switch."""

    def _apply(config: dict) -> None:
        current = _state_from_config(config)
        if current.pipeline == COGNEE_LEGACY:
            return
        if formal_asset_count is None or formal_asset_count > 0:
            raise KnowledgePipelineLocked(
                "Knowledge pipeline is locked because formal assets may exist",
                formal_asset_count=formal_asset_count,
            )
        if current.status not in {
            STATUS_STRUCTURED_PENDING,
            STATUS_STRUCTURED_FAILED,
        }:
            raise KnowledgePipelineLocked(
                f"Knowledge pipeline is locked in state {current.status}",
                formal_asset_count=formal_asset_count,
            )
        config[KNOWLEDGE_PIPELINE_KEY] = COGNEE_LEGACY
        config[KNOWLEDGE_PIPELINE_STATUS_KEY] = STATUS_LEGACY_READY
        config.pop(KNOWLEDGE_PIPELINE_ERROR_KEY, None)
        config.pop(KNOWLEDGE_PIPELINE_RUN_IDENTITY_KEY, None)

    try:
        updated = update_project_config_file_in_state_dir(
            state_dir, _apply, strict=True
        )
    except ProjectConfigReadError as exc:
        raise KnowledgePipelineConfigurationError(str(exc)) from exc
    return _state_from_config(updated)
