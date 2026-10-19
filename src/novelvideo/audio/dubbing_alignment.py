"""把配音长度对齐到分组段落长度的确定性策略链。

顺序固定：先裁掉句间停顿（X3），再在 ±5% 内变速（X2）；仍放不下则阻塞（X4），
交由上层重写台词或拆分段落，而不是无声拉伸。
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_SPEED = 1.05


@dataclass(frozen=True)
class AlignmentResult:
    strategy: str
    speed: float = 1.0
    blocked: bool = False


def align_dialogue(*, audio_seconds: float, segment_seconds: float, pause_budget: float) -> AlignmentResult:
    overrun = audio_seconds - segment_seconds
    if overrun <= 0:
        return AlignmentResult(strategy="none")
    if overrun <= pause_budget:
        return AlignmentResult(strategy="trim_pauses")
    speed = audio_seconds / segment_seconds
    if speed <= MAX_SPEED:
        return AlignmentResult(strategy="speed", speed=round(speed, 3))
    return AlignmentResult(strategy="blocked", speed=1.0, blocked=True)
