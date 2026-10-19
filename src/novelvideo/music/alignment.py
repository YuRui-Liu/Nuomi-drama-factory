"""C 切片（智能配乐）音频模式与分组音频计划契约。

本模块只冻结枚举与字段名，对齐逻辑由 C 切片实现。
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
