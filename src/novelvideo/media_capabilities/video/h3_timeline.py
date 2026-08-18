"""Timeline contracts for multi-segment MiniMax H3 director videos."""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Iterable
from uuid import uuid4


H3_FPS = 24


class DialogueSource(StrEnum):
    EXTERNAL_TTS = "external_tts"
    H3_NATIVE = "h3_native"


@dataclass(frozen=True)
class H3DirectorSegment:
    segment_id: str
    beat_number: int
    prompt: str
    duration_seconds: float
    first_frame: str | None
    last_frame: str | None = None
    dialogue: str = ""
    speaker: str = ""
    tone: str = ""
    voice_style: str = ""
    dialogue_source: DialogueSource = DialogueSource.EXTERNAL_TTS

    def __post_init__(self) -> None:
        object.__setattr__(self, "dialogue_source", DialogueSource(self.dialogue_source))
        if not self.segment_id.strip():
            raise ValueError("segment ID must not be empty")
        if self.beat_number < 1:
            raise ValueError("beat number must be positive")
        if not self.prompt.strip():
            raise ValueError("prompt must not be empty")
        if not math.isfinite(self.duration_seconds) or self.duration_seconds <= 0:
            raise ValueError("duration must be positive and finite")


@dataclass(frozen=True)
class H3TimelineEntry:
    segment: H3DirectorSegment
    start_frame: int
    frame_count: int
    physical_video: str | None = None
    format_version: int = 1
    workflow_id: str | None = None
    provider_task_id: str | None = None

    @property
    def end_frame(self) -> int:
        return self.start_frame + self.frame_count

    @property
    def start_seconds(self) -> float:
        return self.start_frame / H3_FPS

    @property
    def end_seconds(self) -> float:
        return self.end_frame / H3_FPS

    @property
    def actual_duration_seconds(self) -> float:
        return self.frame_count / H3_FPS

    @property
    def dialogue_start_seconds(self) -> float:
        return self.start_seconds

    @property
    def dialogue_end_seconds(self) -> float:
        return self.end_seconds

    @property
    def speaker(self) -> str:
        return self.segment.speaker

    @property
    def dialogue_source(self) -> DialogueSource:
        return self.segment.dialogue_source


@dataclass(frozen=True)
class H3Timeline:
    entries: tuple[H3TimelineEntry, ...]
    fps: int
    total_frames: int

    @property
    def duration_seconds(self) -> float:
        return self.total_frames / self.fps


H3CompiledTimeline = H3Timeline


@dataclass(frozen=True)
class H3DirectorOutputManifest:
    physical_video: str
    entries: tuple[H3TimelineEntry, ...]
    fps: int = H3_FPS
    total_frames: int = 0
    format_version: int = 1
    workflow_id: str | None = None
    provider_task_id: str | None = None
    original_audio_path: str | None = None
    original_audio_status: str = "not_requested"
    dialogue_stem_path: str | None = None
    dialogue_stem_status: str = "not_requested"
    ambience_stem_path: str | None = None
    ambience_stem_status: str = "not_requested"

    def __post_init__(self) -> None:
        if not self.physical_video.strip():
            raise ValueError("physical video must not be empty")
        normalized = tuple(
            replace(
                entry,
                physical_video=self.physical_video,
                format_version=self.format_version,
                workflow_id=entry.workflow_id or self.workflow_id,
                provider_task_id=entry.provider_task_id or self.provider_task_id,
            )
            for entry in self.entries
        )
        object.__setattr__(self, "entries", normalized)
        expected_total = normalized[-1].end_frame if normalized else 0
        if self.total_frames == 0:
            object.__setattr__(self, "total_frames", expected_total)
        elif self.total_frames != expected_total:
            raise ValueError("manifest total frames do not match entries")

    @property
    def actual_duration_seconds(self) -> float:
        return self.total_frames / self.fps


# Backwards-compatible name used by the first integration draft.
H3DirectorManifest = H3DirectorOutputManifest


def legal_frame_count(duration_seconds: float, *, fps: int = H3_FPS) -> int:
    """Return the smallest H3-legal ``17k + 5`` count meeting the duration."""
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("duration must be positive and finite")
    requested = math.ceil(duration_seconds * fps)
    k = max(0, math.ceil((requested - 5) / 17))
    return 17 * k + 5


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
        frames = legal_frame_count(segment.duration_seconds)
        entries.append(H3TimelineEntry(segment, start, frames))
        start += frames
    return H3Timeline(tuple(entries), H3_FPS, start)


# Public names from the director-output contract.
frames_for_duration = legal_frame_count
build_h3_timeline_data = compile_h3_timeline


def save_h3_director_manifest(
    path: Path | str, manifest: H3DirectorOutputManifest
) -> None:
    """Atomically save a manifest beside its final destination."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.parent / f".{target.name}.{uuid4().hex}.tmp"
    payload = asdict(manifest)
    payload["actual_duration_seconds"] = manifest.actual_duration_seconds
    for serialized, entry in zip(payload["entries"], manifest.entries, strict=True):
        serialized.update(
            start_seconds=entry.start_seconds,
            end_seconds=entry.end_seconds,
            actual_duration_seconds=entry.actual_duration_seconds,
            dialogue_start_seconds=entry.dialogue_start_seconds,
            dialogue_end_seconds=entry.dialogue_end_seconds,
            speaker=entry.speaker,
            dialogue_source=entry.dialogue_source.value,
        )
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def load_h3_director_manifest(path: Path | str) -> H3DirectorManifest:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = []
    for item in payload["entries"]:
        segment = H3DirectorSegment(**item["segment"])
        entries.append(
            H3TimelineEntry(
                segment=segment,
                start_frame=int(item["start_frame"]),
                frame_count=int(item["frame_count"]),
                physical_video=item.get("physical_video"),
                format_version=int(item.get("format_version", 1)),
                workflow_id=item.get("workflow_id"),
                provider_task_id=item.get("provider_task_id"),
            )
        )
    return H3DirectorOutputManifest(
        physical_video=payload["physical_video"],
        entries=tuple(entries),
        fps=int(payload["fps"]),
        total_frames=int(payload["total_frames"]),
        format_version=int(payload.get("format_version", 1)),
        workflow_id=payload.get("workflow_id"),
        provider_task_id=payload.get("provider_task_id"),
        original_audio_path=payload.get("original_audio_path"),
        original_audio_status=payload.get("original_audio_status", "not_requested"),
        dialogue_stem_path=payload.get("dialogue_stem_path"),
        dialogue_stem_status=payload.get("dialogue_stem_status", "not_requested"),
        ambience_stem_path=payload.get("ambience_stem_path"),
        ambience_stem_status=payload.get("ambience_stem_status", "not_requested"),
    )


save_director_manifest = save_h3_director_manifest


__all__ = [
    "DialogueSource",
    "H3DirectorManifest",
    "H3DirectorOutputManifest",
    "H3DirectorSegment",
    "H3CompiledTimeline",
    "H3Timeline",
    "H3TimelineEntry",
    "build_h3_timeline_data",
    "compile_h3_timeline",
    "frames_for_duration",
    "legal_frame_count",
    "load_h3_director_manifest",
    "save_director_manifest",
    "save_h3_director_manifest",
    "validate_director_segments",
]
