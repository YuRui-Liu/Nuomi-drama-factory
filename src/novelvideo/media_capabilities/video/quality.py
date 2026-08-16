"""Pure quality checks for generated video candidates."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from novelvideo.media_capabilities.models import VideoGenerationRequest


class VideoProbe(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    duration: float = Field(ge=0)
    width: int
    height: int
    fps: float
    has_audio: bool


class VideoQualityIssue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str


def _requested_resolution(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    match = re.fullmatch(r"(\d+)x(\d+)", value.strip().lower())
    return (int(match.group(1)), int(match.group(2))) if match else None


def validate_video(
    probe: VideoProbe,
    request: VideoGenerationRequest,
    *,
    first_frame_similarity: float | None = None,
    minimum_frame_similarity: float = 0.9,
) -> tuple[VideoQualityIssue, ...]:
    issues: list[VideoQualityIssue] = []
    if probe.width <= 0 or probe.height <= 0 or probe.fps <= 0:
        issues.append(VideoQualityIssue(code="video.stream_invalid"))
    if abs(probe.duration - request.duration) > max(0.5, request.duration * 0.1):
        issues.append(VideoQualityIssue(code="video.duration_mismatch"))
    expected = _requested_resolution(request.resolution)
    if expected is not None and (probe.width, probe.height) != expected:
        issues.append(VideoQualityIssue(code="video.resolution_mismatch"))
    if request.generate_audio and not probe.has_audio:
        issues.append(VideoQualityIssue(code="video.audio_missing"))
    if first_frame_similarity is not None and first_frame_similarity < minimum_frame_similarity:
        issues.append(VideoQualityIssue(code="video.first_frame_mismatch"))
    return tuple(issues)


__all__ = ["VideoProbe", "VideoQualityIssue", "validate_video"]
