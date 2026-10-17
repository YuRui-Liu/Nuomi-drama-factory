"""Immutable contracts for narrative-group dialogue takes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FrozenContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DialogueSnapshot(FrozenContract):
    dialogue_id: str = Field(min_length=1)
    character_id: str = Field(min_length=1)
    identity_id: str = ""
    speaker: str = ""
    script_revision: str = ""
    source_revision: str = ""
    group_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    voice_version_id: str = ""
    target_duration_seconds: float | None = Field(default=None, gt=0)
    performance: dict[str, Any] = Field(default_factory=dict)
    source_key: str = ""
    source_status: Literal["active", "deleted", "needs_review"] = "active"


class VoiceVersionBinding(FrozenContract):
    character_id: str = Field(min_length=1)
    identity_id: str = ""
    version_id: str = Field(min_length=1)


class DubbingPreparation(FrozenContract):
    preparation_id: str = Field(min_length=1)
    episode: int = Field(ge=1)
    group_id: str = Field(min_length=1)
    script_revision: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)
    expected_revision: int = Field(ge=0)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    dialogue_ids: tuple[str, ...]
    requested_dialogue_ids: tuple[str, ...] | None = None
    voice_versions: tuple[VoiceVersionBinding, ...] = ()
    request_fingerprint: str = ""
    input_fingerprint: str = ""
    source_dialogue_ids: tuple[str, ...] = ()
    dialogues: tuple[DialogueSnapshot, ...]
    source_dialogues: tuple[DialogueSnapshot, ...] = ()
    missing_voices: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()


class DubbingTake(FrozenContract):
    take_id: str = Field(min_length=1)
    preparation_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    dialogue_id: str = Field(min_length=1)
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    voice_version_id: str = Field(min_length=1)
    audio_path: str = Field(min_length=1)
    audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    actual_duration_seconds: float = Field(gt=0)
    target_duration_seconds: float | None = Field(default=None, gt=0)
    duration_delta_seconds: float | None = None
    provider: str = "indextts2"
    model: str = "IndexTTS2-v2"
    workflow_version: str = ""
    compiler_version: str = ""
    effective_parameters: dict[str, Any] = Field(default_factory=dict)
    technical_status: Literal["passed", "failed"] = "passed"
    status: Literal["succeeded", "failed"] = "succeeded"
    error: str = ""


class DubbingTask(FrozenContract):
    task_id: str = Field(min_length=1)
    preparation_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    dialogue_ids: tuple[str, ...] = ()
    status: Literal[
        "submission_unknown", "queued", "running", "partial_failure", "completed", "failed"
    ] = "submission_unknown"
    backend_task_id: str = ""
    failed_dialogue_ids: tuple[str, ...] = ()
    error: str = ""


class DubbingPrepareRequest(FrozenContract):
    preparation_id: str | None = Field(default=None, min_length=1, max_length=160)
    script_revision: str = Field(min_length=1)
    dialogue_ids: list[str] | None = None
    voice_versions: list[VoiceVersionBinding] = Field(default_factory=list)
    performance: dict[str, dict[str, Any]] = Field(default_factory=dict)


class DubbingGenerateRequest(FrozenContract):
    preparation_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=1)


class DubbingAdoptRequest(FrozenContract):
    take_ids: list[str] = Field(min_length=1)
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=1)


class DubbingRetryRequest(FrozenContract):
    task_id: str = Field(min_length=1)
    failed_dialogue_ids: list[str] = Field(min_length=1)
    request_id: str = Field(min_length=1)


__all__ = [
    "DialogueSnapshot",
    "DubbingAdoptRequest",
    "DubbingGenerateRequest",
    "DubbingPreparation",
    "DubbingPrepareRequest",
    "DubbingRetryRequest",
    "DubbingTake",
    "DubbingTask",
    "VoiceVersionBinding",
]
