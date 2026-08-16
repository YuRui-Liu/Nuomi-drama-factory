"""Deterministic PCM WAV validation and batch dialogue merging."""

from __future__ import annotations

import audioop
import wave
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class AudioProbe(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sample_rate: int
    channels: int
    sample_width: int
    frame_count: int


class AudioQualityIssue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str


class AudioSegment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int = Field(ge=0)
    path: Path


def probe_audio(path: str | Path) -> AudioProbe:
    with wave.open(str(path), "rb") as stream:
        return AudioProbe(
            sample_rate=stream.getframerate(),
            channels=stream.getnchannels(),
            sample_width=stream.getsampwidth(),
            frame_count=stream.getnframes(),
        )


def validate_audio(probe: AudioProbe) -> tuple[AudioQualityIssue, ...]:
    issues: list[AudioQualityIssue] = []
    if probe.frame_count <= 0 or probe.sample_rate <= 0:
        issues.append(AudioQualityIssue(code="audio.empty"))
    if probe.channels != 1:
        issues.append(AudioQualityIssue(code="audio.channels_unsupported"))
    if probe.sample_width != 2:
        issues.append(AudioQualityIssue(code="audio.sample_width_unsupported"))
    return tuple(issues)


def _read_pcm(segment: AudioSegment, target_rate: int) -> bytes:
    with wave.open(str(segment.path), "rb") as stream:
        probe = AudioProbe(
            sample_rate=stream.getframerate(),
            channels=stream.getnchannels(),
            sample_width=stream.getsampwidth(),
            frame_count=stream.getnframes(),
        )
        issues = validate_audio(probe)
        if issues:
            raise ValueError(issues[0].code)
        pcm = stream.readframes(stream.getnframes())
    if probe.sample_rate != target_rate:
        pcm, _ = audioop.ratecv(pcm, 2, 1, probe.sample_rate, target_rate, None)
        expected_bytes = round(probe.frame_count * target_rate / probe.sample_rate) * 2
        if len(pcm) < expected_bytes:
            pcm += b"\0" * (expected_bytes - len(pcm))
        elif len(pcm) > expected_bytes:
            pcm = pcm[:expected_bytes]
    return pcm


def merge_segments(
    segments: list[AudioSegment],
    output: str | Path,
    *,
    pause_ms: int = 0,
    crossfade_ms: int = 0,
    sample_rate: int | None = None,
) -> Path:
    ordered = sorted(segments, key=lambda item: item.index)
    if not ordered or [item.index for item in ordered] != list(range(len(ordered))):
        raise ValueError("audio.segment_missing")
    if pause_ms < 0 or crossfade_ms < 0:
        raise ValueError("audio.invalid_timing")

    target_rate = sample_rate or max(probe_audio(item.path).sample_rate for item in ordered)
    chunks = [_read_pcm(item, target_rate) for item in ordered]
    pause = b"\0\0" * round(target_rate * pause_ms / 1000)
    crossfade_frames = round(target_rate * crossfade_ms / 1000)
    merged = chunks[0]
    for chunk in chunks[1:]:
        if crossfade_frames:
            overlap_bytes = min(crossfade_frames * 2, len(merged), len(chunk))
            overlap_bytes -= overlap_bytes % 2
            if overlap_bytes:
                mixed = audioop.add(
                    audioop.mul(merged[-overlap_bytes:], 2, 0.5),
                    audioop.mul(chunk[:overlap_bytes], 2, 0.5),
                    2,
                )
                merged = merged[:-overlap_bytes] + pause + mixed + chunk[overlap_bytes:]
                continue
        merged += pause + chunk

    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(destination), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(target_rate)
        stream.writeframes(merged)
    return destination


__all__ = [
    "AudioProbe",
    "AudioQualityIssue",
    "AudioSegment",
    "merge_segments",
    "probe_audio",
    "validate_audio",
]
