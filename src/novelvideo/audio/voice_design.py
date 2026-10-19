"""C 切片（配音师）VoiceDesignBrief 接口契约。

本模块只冻结字段名与函数签名，brief 生成逻辑由 C 切片实现。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class VoiceDesignBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    character_id: str
    instruct_zh: str = ""
    instruct_en: str = ""
    language: str = "Chinese"
    audition_text: str = ""
    negatives: list[str] = Field(default_factory=list)


def build_voice_design_brief(*args, **kwargs) -> VoiceDesignBrief:
    raise NotImplementedError("C slice implements build_voice_design_brief")
