"""C 切片（配音师）VoiceDesignBrief 与 IndexTTS2 克隆表演分层。

音色 brief 只描述身份与当前状态，不把场景情绪写死成音色；克隆参数只开放
表演层（语速、句内停顿），身份层由参考音固定，不加强情绪权重。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

_SPEAKING_RATE_MIN = 0.9
_SPEAKING_RATE_MAX = 1.1


class VoiceDesignBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    character_id: str
    instruct_zh: str = ""
    instruct_en: str = ""
    language: str = "Chinese"
    audition_text: str = ""
    negatives: list[str] = Field(default_factory=list)


def _clean(value: str) -> str:
    return str(value or "").strip()


def build_voice_design_brief(*, character_id: str, gender: str, age: int, register: str,
                             texture: str = "", resonance: str = "",
                             articulation: str = "", pace: str = "", accent: str = "",
                             baseline: str = "", state: str = "",
                             negatives: list[str] | None = None) -> VoiceDesignBrief:
    """Build the Qwen3 voice-design instruction in the fixed spec order.

    Gender, age and register are identity facts and are required; the emotional
    baseline is a current state, never a fixed timbre.
    """
    if not _clean(gender) or not _clean(register) or int(age) <= 0:
        raise ValueError("voice_brief_requires gender, age and register")

    head = "，".join(
        part
        for part in (
            _clean(gender),
            f"{int(age)}岁",
            _clean(register),
            _clean(texture),
            _clean(resonance),
            _clean(articulation),
            _clean(pace),
            _clean(accent),
        )
        if part
    )
    tail: list[str] = []
    if _clean(baseline):
        tail.append(f"情绪基线：{_clean(baseline)}")
    if _clean(state):
        tail.append(f"本剧状态：{_clean(state)}")
    excluded = [_clean(item) for item in (negatives or []) if _clean(item)]
    if excluded:
        tail.append("避免：" + "、".join(excluded))

    instruct = "；".join([head, *tail]) + "。"
    return VoiceDesignBrief(
        character_id=character_id,
        instruct_zh=instruct,
        negatives=list(negatives or []),
    )


def clone_performance(*, speaking_rate: float, pause_seconds: float) -> dict:
    """Only the performance layer is tunable; emotion weights would hurt identity.

    The spec recommends speakingRate 0.9-1.1 and never sends an emotion weight
    to IndexTTS2 in this path.
    """
    if not _SPEAKING_RATE_MIN <= speaking_rate <= _SPEAKING_RATE_MAX:
        raise ValueError("speakingRate must stay within 0.9-1.1")
    if pause_seconds < 0:
        raise ValueError("maxInternalPauseDuration must be non-negative")
    return {"speakingRate": speaking_rate, "maxInternalPauseDuration": pause_seconds}


__all__ = ["VoiceDesignBrief", "build_voice_design_brief", "clone_performance"]
