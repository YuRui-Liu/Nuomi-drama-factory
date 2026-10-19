"""音频模式、分组音频计划契约与互斥校验。

枚举与字段名由波次 0 冻结；`validate_audio_plan` 校验同一分组只能选择
一种音频来源（原声 / 配音 / 混合）。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class AudioMode(str, Enum):
    DUB = "dub"
    ORIGINAL = "original"
    HYBRID = "hybrid"


class GroupAudioPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: str
    audio_mode: AudioMode = AudioMode.DUB
    replaced_line_ids: list[str] = Field(default_factory=list)
    music_enabled: bool = False


class AudioIssue(BaseModel):
    code: str
    message: str


def validate_audio_plan(plan: GroupAudioPlan) -> list[AudioIssue]:
    issues: list[AudioIssue] = []
    if plan.audio_mode is AudioMode.HYBRID and not plan.replaced_line_ids:
        issues.append(AudioIssue(code="audio_mode_conflict", message="hybrid requires replaced_line_ids"))
    if plan.audio_mode is AudioMode.ORIGINAL and plan.replaced_line_ids:
        issues.append(AudioIssue(code="audio_mode_conflict", message="original mode cannot replace lines"))
    return issues
