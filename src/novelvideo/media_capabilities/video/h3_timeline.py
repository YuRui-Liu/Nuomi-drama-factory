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

    @property
    def end_frame(self) -> int:
        return self.start_frame + self.frame_count


@dataclass(frozen=True)
class H3Timeline:
    entries: tuple[H3TimelineEntry, ...]
    fps: int
    total_frames: int

    @property
    def duration_seconds(self) -> float:
        return self.total_frames / self.fps


@dataclass(frozen=True)
class H3DirectorManifest:
    physical_video: str
    entries: tuple[H3TimelineEntry, ...]
    fps: int = H3_FPS
    total_frames: int = 0

    def __post_init__(self) -> None:
        if not self.physical_video.strip():
            raise ValueError("physical video must not be empty")
        normalized = tuple(
            replace(entry, physical_video=self.physical_video) for entry in self.entries
        )
        object.__setattr__(self, "entries", normalized)
        expected_total = normalized[-1].end_frame if normalized else 0
        if self.total_frames == 0:
            object.__setattr__(self, "total_frames", expected_total)
        elif self.total_frames != expected_total:
            raise ValueError("manifest total frames do not match entries")


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


def save_h3_director_manifest(path: Path | str, manifest: H3DirectorManifest) -> None:
    """Atomically save a manifest beside its final destination."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.parent / f".{target.name}.{uuid4().hex}.tmp"
    payload = asdict(manifest)
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
            )
        )
    return H3DirectorManifest(
        physical_video=payload["physical_video"],
        entries=tuple(entries),
        fps=int(payload["fps"]),
        total_frames=int(payload["total_frames"]),
    )


__all__ = [
    "DialogueSource",
    "H3DirectorManifest",
    "H3DirectorSegment",
    "H3Timeline",
    "H3TimelineEntry",
    "compile_h3_timeline",
    "legal_frame_count",
    "load_h3_director_manifest",
    "save_h3_director_manifest",
    "validate_director_segments",
]
