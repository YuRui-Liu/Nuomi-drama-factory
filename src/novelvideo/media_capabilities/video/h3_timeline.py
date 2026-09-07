"""Timeline contracts for multi-segment MiniMax H3 director videos."""

from __future__ import annotations

import json
import math
import os
from copy import deepcopy
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


H3_FPS = 24
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class DialogueSource(StrEnum):
    EXTERNAL_TTS = "external_tts"
    H3_NATIVE = "h3_native"


class H3TransitionRule(BaseModel):
    model_config = _MODEL_CONFIG
    kind: Literal["hard_cut", "dissolve"] = "hard_cut"
    frames: int = Field(default=0, ge=0)
    audio: Literal["none", "j_cut", "l_cut"] = "none"
    audio_ms: int = Field(default=0, ge=0, le=500)
    source: str = Field(min_length=1)


class H3GenerationAttemptEvidence(BaseModel):
    model_config = _MODEL_CONFIG
    attempt: int = Field(gt=0)
    status: Literal[
        "submitted", "completed", "transport_failed", "quality_rejected"
    ]
    provider_task_id: str | None = None
    error_code: str | None = None


class H3ObservedBoundary(BaseModel):
    model_config = _MODEL_CONFIG
    value: str = Field(min_length=1)
    source_contract_revision: int = Field(gt=0)
    result_contract_revision: int = Field(gt=0)
    accepted: bool = False
    deviation_reason: str = ""
    lock_violations: tuple[
        Literal["identity", "spatial", "prop", "camera", "lighting"], ...
    ] = ()

    @field_validator("value", mode="before")
    @classmethod
    def trim_value(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


def transition_for(
    relation: str,
    *,
    has_leading_dialogue: bool = False,
    has_trailing_ambience: bool = False,
) -> H3TransitionRule:
    relation = relation.strip() or "single"
    kind: Literal["hard_cut", "dissolve"] = (
        "dissolve" if relation == "time_jump" else "hard_cut"
    )
    frames = 8 if kind == "dissolve" else 0
    if has_leading_dialogue:
        audio, audio_ms = "j_cut", 300
    elif has_trailing_ambience:
        audio, audio_ms = "l_cut", 500
    else:
        audio, audio_ms = "none", 0
    return H3TransitionRule(
        kind=kind,
        frames=frames,
        audio=audio,
        audio_ms=audio_ms,
        source=f"relation:{relation}",
    )


class H3DirectorSegment(BaseModel):
    model_config = _MODEL_CONFIG
    segment_id: str = Field(min_length=1)
    source_shot_ids: tuple[str, ...] = Field(default=(), max_length=2)
    beat_number: int = Field(gt=0)
    prompt: str = Field(min_length=1)
    duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    first_frame: str | None
    last_frame: str | None = None
    dialogue: str = ""
    speaker: str = ""
    tone: str = ""
    voice_style: str = ""
    dialogue_source: DialogueSource = DialogueSource.EXTERNAL_TTS

    @field_validator("segment_id", "prompt", mode="before")
    @classmethod
    def trim_required_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("source_shot_ids")
    @classmethod
    def validate_source_shot_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(shot_id.strip() for shot_id in value)
        if any(not shot_id for shot_id in normalized):
            raise ValueError("source shot IDs must not be blank")
        return normalized

    @field_validator("first_frame", "last_frame", mode="before")
    @classmethod
    def trim_optional_frame(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("frame path must not be blank")
        return value


def source_shot_ids_for(segment: H3DirectorSegment) -> tuple[str, ...]:
    """Return structured IDs, falling back only for legacy manifests."""
    return segment.source_shot_ids or tuple(segment.segment_id.split("--"))

class H3TimelineEntry(BaseModel):
    model_config = _MODEL_CONFIG
    segment: H3DirectorSegment
    start_frame: int = Field(ge=0)
    frame_count: int = Field(gt=0)
    physical_video: str | None = None
    format_version: int = 1
    workflow_id: str | None = None
    provider_task_id: str | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None
    actual_duration_seconds: float | None = None
    dialogue_start_seconds: float | None = None
    dialogue_end_seconds: float | None = None
    speaker: str | None = None
    dialogue_source: DialogueSource | None = None
    director_plan: dict[str, Any] | None = None
    prompt_profile: dict[str, Any] | None = None
    quality_report: dict[str, Any] | None = None
    input_summary: dict[str, Any] | None = None
    continuity_contracts: tuple[dict[str, Any], ...] = ()
    risk_report: dict[str, Any] | None = None
    mode_decision: dict[str, Any] | None = None
    compiled_bundle: dict[str, Any] | None = None
    attempts: tuple[H3GenerationAttemptEvidence, ...] = ()
    observed_carry_out: H3ObservedBoundary | None = None
    status: Literal[
        "planned",
        "submitted",
        "generated",
        "completed",
        "partial_failure",
        "quality_rejected",
        "transport_failed",
        "postprocess_failed",
        "quality_mismatch",
        "partial_failure",
    ] = "completed"

    @field_validator(
        "continuity_contracts", "risk_report", "mode_decision", "compiled_bundle",
        mode="before",
    )
    @classmethod
    def snapshot_continuity_evidence(cls, value: object) -> object:
        return deepcopy(value)

    @field_validator("attempts")
    @classmethod
    def validate_attempt_sequence(
        cls, value: tuple[H3GenerationAttemptEvidence, ...]
    ) -> tuple[H3GenerationAttemptEvidence, ...]:
        if [item.attempt for item in value] != list(range(1, len(value) + 1)):
            raise ValueError("attempts must be contiguous from 1")
        return value

    @model_validator(mode="after")
    def derive_metadata(self) -> "H3TimelineEntry":
        start = self.start_frame / H3_FPS
        end = (self.start_frame + self.frame_count) / H3_FPS
        derived = {
            "start_seconds": start,
            "end_seconds": end,
            "actual_duration_seconds": self.frame_count / H3_FPS,
            "dialogue_start_seconds": start,
            "dialogue_end_seconds": end,
            "speaker": self.segment.speaker,
            "dialogue_source": self.segment.dialogue_source,
        }
        for name, value in derived.items():
            supplied = getattr(self, name)
            if supplied is not None and supplied != value:
                raise ValueError(f"entry {name} does not match frame boundaries")
            object.__setattr__(self, name, value)
        return self

    @property
    def end_frame(self) -> int:
        return self.start_frame + self.frame_count

class H3CompiledTimeline(BaseModel):
    model_config = _MODEL_CONFIG
    entries: tuple[H3TimelineEntry, ...] = Field(min_length=1)
    fps: Literal[24] = H3_FPS
    total_frames: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_boundaries(self) -> "H3CompiledTimeline":
        expected = 0
        for entry in self.entries:
            if entry.start_frame != expected:
                raise ValueError("timeline entries must be contiguous from frame 0")
            expected = entry.end_frame
        if self.total_frames == 0:
            object.__setattr__(self, "total_frames", expected)
        elif expected != self.total_frames:
            raise ValueError("last timeline entry must end at total frames")
        return self

    @property
    def duration_seconds(self) -> float:
        return self.total_frames / self.fps


H3Timeline = H3CompiledTimeline


class H3ReferenceManifestEntry(BaseModel):
    """Serializable evidence for one frozen global reference."""

    model_config = _MODEL_CONFIG
    picture_index: int = Field(ge=1)
    reference_id: str = Field(min_length=1)
    source_kind: Literal[
        "character_identity",
        "scene_master",
        "prop_reference",
        "temporary_upload",
    ]
    label: str = Field(min_length=1)
    subject_description: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator(
        "reference_id", "source_kind", "label", "subject_description", mode="before"
    )
    @classmethod
    def trim_reference_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("sha256", mode="before")
    @classmethod
    def normalize_reference_sha256(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class H3DirectorOutputManifest(BaseModel):
    model_config = _MODEL_CONFIG
    physical_video: str | None = Field(default=None, min_length=1)
    entries: tuple[H3TimelineEntry, ...] = Field(min_length=1)
    fps: Literal[24] = H3_FPS
    total_frames: int = Field(default=0, ge=0)
    format_version: int = Field(default=2, gt=0)
    workflow_id: str | None = None
    provider_workflow_id: str | None = None
    provider_task_id: str | None = None
    reference_settings_revision: int | None = Field(default=None, ge=0)
    reference_limit: int | None = Field(default=None, ge=1, le=10)
    global_references: tuple[H3ReferenceManifestEntry, ...] = ()
    workflow_parameters: dict[str, str] = Field(default_factory=dict)
    provider_parameters: dict[str, object] = Field(default_factory=dict)
    transition_rules: tuple[H3TransitionRule, ...] = ()
    actual_output: dict[str, int] = Field(default_factory=dict)
    original_audio_path: str | None = None
    original_audio_status: str = "not_requested"
    dialogue_stem_path: str | None = None
    dialogue_stem_status: str = "not_requested"
    ambience_stem_path: str | None = None
    ambience_stem_status: str = "not_requested"
    actual_duration_seconds: float | None = None
    status: Literal[
        "planned",
        "submitted",
        "generated",
        "completed",
        "partial_failure",
        "quality_rejected",
        "transport_failed",
        "postprocess_failed",
        "quality_mismatch",
    ] = "completed"

    @field_validator(
        "physical_video", "workflow_id", "provider_workflow_id", "provider_task_id",
        "original_audio_path", "dialogue_stem_path", "ambience_stem_path",
        mode="before",
    )
    @classmethod
    def trim_optional_text(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("value must not be blank")
        return value

    @field_validator(
        "workflow_parameters", "provider_parameters", "actual_output", mode="before"
    )
    @classmethod
    def snapshot_parameters(cls, value: object) -> object:
        return deepcopy(value)

    @model_validator(mode="after")
    def validate_and_normalize(self) -> "H3DirectorOutputManifest":
        expected_indexes = tuple(range(1, len(self.global_references) + 1))
        if tuple(item.picture_index for item in self.global_references) != expected_indexes:
            raise ValueError("global reference picture indexes must be contiguous from 1")
        reference_ids = tuple(item.reference_id for item in self.global_references)
        reference_hashes = tuple(item.sha256 for item in self.global_references)
        if len(reference_ids) != len(set(reference_ids)):
            raise ValueError("global reference IDs must be unique")
        if len(reference_hashes) != len(set(reference_hashes)):
            raise ValueError("global reference hashes must be unique")
        if (
            self.reference_limit is not None
            and len(self.global_references) > self.reference_limit
        ):
            raise ValueError("global references exceed reference_limit")
        normalized = tuple(
            entry.model_copy(
                update={
                    "physical_video": self.physical_video,
                    "format_version": self.format_version,
                    "workflow_id": entry.workflow_id or self.workflow_id,
                    "provider_task_id": entry.provider_task_id or self.provider_task_id,
                }
            )
            for entry in self.entries
        )
        timeline = H3CompiledTimeline(
            entries=normalized, fps=self.fps, total_frames=self.total_frames
        )
        if self.total_frames == 0:
            object.__setattr__(self, "total_frames", timeline.total_frames)
        object.__setattr__(self, "entries", normalized)
        duration = self.total_frames / self.fps
        if self.actual_duration_seconds is not None and self.actual_duration_seconds != duration:
            raise ValueError("manifest actual duration does not match total frames")
        object.__setattr__(self, "actual_duration_seconds", duration)
        return self


# Backwards-compatible name used by the first integration draft.
H3DirectorManifest = H3DirectorOutputManifest


def frames_for_duration(duration_seconds: float, fps: int = H3_FPS) -> int:
    """Return the smallest H3-legal ``17k + 5`` count meeting the duration."""
    if fps <= 0:
        raise ValueError("fps must be positive")
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("duration must be positive and finite")
    requested = math.ceil(duration_seconds * fps)
    k = max(0, math.ceil((requested - 5) / 17))
    return 17 * k + 5


legal_frame_count = frames_for_duration


def validate_director_segments(
    segments: Iterable[H3DirectorSegment], *, strict_first_frame: bool = False
) -> tuple[H3DirectorSegment, ...]:
    normalized = tuple(segments)
    if not normalized:
        raise ValueError("at least one director segment is required")
    seen: set[str] = set()
    for segment in normalized:
        if segment.segment_id in seen:
            raise ValueError(f"duplicate segment ID: {segment.segment_id}")
        seen.add(segment.segment_id)
        if strict_first_frame and not segment.first_frame:
            raise ValueError(f"segment {segment.segment_id} requires a first frame")
        if not segment.first_frame and not segment.last_frame:
            raise ValueError(f"segment {segment.segment_id} requires a first or last frame")
    return normalized


def compile_h3_timeline(
    segments: Iterable[H3DirectorSegment], *, strict_first_frame: bool = False
) -> H3Timeline:
    normalized = validate_director_segments(
        segments, strict_first_frame=strict_first_frame
    )
    start = 0
    entries: list[H3TimelineEntry] = []
    for segment in normalized:
        frames = frames_for_duration(segment.duration_seconds, H3_FPS)
        entries.append(
            H3TimelineEntry(segment=segment, start_frame=start, frame_count=frames)
        )
        start += frames
    return H3CompiledTimeline(entries=tuple(entries), fps=H3_FPS, total_frames=start)


# Public names from the director-output contract.
build_h3_timeline_data = compile_h3_timeline


def save_h3_director_manifest(
    path: Path | str, manifest: H3DirectorOutputManifest
) -> None:
    """Atomically save a manifest beside its final destination."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.parent / f".{target.name}.{uuid4().hex}.tmp"
    payload = manifest.model_dump(mode="json")
    try:
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def load_h3_director_manifest(path: Path | str) -> H3DirectorManifest:
    return H3DirectorOutputManifest.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


save_director_manifest = save_h3_director_manifest


__all__ = [
    "DialogueSource",
    "H3DirectorManifest",
    "H3DirectorOutputManifest",
    "H3ReferenceManifestEntry",
    "H3DirectorSegment",
    "H3GenerationAttemptEvidence",
    "H3ObservedBoundary",
    "H3CompiledTimeline",
    "H3Timeline",
    "H3TimelineEntry",
    "H3TransitionRule",
    "build_h3_timeline_data",
    "compile_h3_timeline",
    "frames_for_duration",
    "legal_frame_count",
    "load_h3_director_manifest",
    "save_director_manifest",
    "save_h3_director_manifest",
    "transition_for",
    "validate_director_segments",
]
