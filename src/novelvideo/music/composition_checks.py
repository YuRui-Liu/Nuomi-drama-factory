"""合成前校验：时间线无缺口、音轨不重复。

只做确定性检查，不触碰 ffmpeg；发现的问题在合成前阻塞，避免产出缺段或混音错误的成片。
"""

from __future__ import annotations

from pydantic import BaseModel


class CompositionIssue(BaseModel):
    code: str
    message: str


def check_timeline(*, segments: list[dict], tracks: list[str]) -> list[CompositionIssue]:
    issues: list[CompositionIssue] = []
    for segment in segments:
        if not segment.get("video"):
            issues.append(CompositionIssue(code="timeline_gap", message=f"missing video: {segment.get('id')}"))
    if len(tracks) != len(set(tracks)):
        issues.append(CompositionIssue(code="duplicate_audio_track", message="audio track passed twice"))
    return issues
