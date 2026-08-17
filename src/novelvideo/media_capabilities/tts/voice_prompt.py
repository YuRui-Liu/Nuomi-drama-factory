"""Compile Qwen3-TTS voice-design instructions."""

from __future__ import annotations

import re

from novelvideo.media_capabilities.tts.models import VoiceSpec, VoiceSpecError


_REAL_PERSON_PATTERNS = (
    re.compile(r"\b(?:sounds? like|in the voice of|imitate)\b", re.IGNORECASE),
    re.compile(r"(?:模仿|仿照).{1,30}(?:声音|嗓音|声线)"),
)


def _clean(value: str) -> str:
    return " ".join(value.split())


def _validate(spec: VoiceSpec) -> None:
    values = [
        spec.age_impression,
        spec.pitch,
        spec.texture,
        spec.resonance,
        spec.articulation,
        spec.pace,
        spec.accent,
        spec.emotional_baseline,
        *spec.negative_constraints,
    ]
    if any(pattern.search(value) for value in values for pattern in _REAL_PERSON_PATTERNS):
        raise VoiceSpecError("voice specification must not reference a real person")

    pitch = _clean(spec.pitch).lower()
    if ("high" in pitch and "low" in pitch) or ("高音" in pitch and "低音" in pitch):
        raise VoiceSpecError("pitch is contradictory")
    pace = _clean(spec.pace).lower()
    if ("fast" in pace and "slow" in pace) or ("快速" in pace and "缓慢" in pace):
        raise VoiceSpecError("pace is contradictory")


def compile_voice_instruction(spec: VoiceSpec) -> str:
    _validate(spec)
    dimensions = (
        ("age impression", spec.age_impression),
        ("pitch", spec.pitch),
        ("vocal texture", spec.texture),
        ("resonance", spec.resonance),
        ("articulation", spec.articulation),
        ("pace", spec.pace),
        ("accent", spec.accent),
        ("emotional baseline", spec.emotional_baseline),
    )
    clauses = [f"{label}: {cleaned}" for label, value in dimensions if (cleaned := _clean(value))]
    instruction = "Create a voice with the following characteristics: " + "; ".join(clauses) + "."
    negatives = [_clean(value) for value in spec.negative_constraints if _clean(value)]
    if negatives:
        instruction += " Avoid: " + ", ".join(negatives) + "."
    return instruction


def compile_character_voice_description(
    *, gender: str = "", age_group: str = "", role: str = "", raw_description: str = ""
) -> str:
    """Build a compact Qwen3 VoiceDesign instruction from audible attributes only."""
    raw = _clean(raw_description)
    _validate(VoiceSpec(texture=raw))
    gender_label = "女性" if gender.lower() in {"female", "女", "woman"} else (
        "男性" if gender.lower() in {"male", "男", "man"} else "中性"
    )
    age_label = {
        "child": "儿童", "teen": "青少年", "youth": "青年",
        "young": "青年", "middle": "中年", "elder": "老年",
    }.get(age_group.lower(), "青年")
    role_text = _clean(role)
    if age_label in {"儿童", "青少年"}:
        pitch, texture, pace = "中高音", "清澈自然", "语速稍快，节奏灵活"
    elif age_label == "老年":
        pitch, texture, pace = "低音", "浑厚微沙哑", "语速缓慢，气息平稳"
    elif any(word in role_text for word in ("反派", "冷峻", "威严", "强势")):
        pitch, texture, pace = "中低音", "沉稳略带颗粒感", "语速偏慢，停顿明确"
    elif gender_label == "女性":
        pitch, texture, pace = "中高音", "清亮温润", "语速中等，节奏自然"
    else:
        pitch, texture, pace = "中低音", "温暖沉稳", "语速中等，停顿自然"
    emotion = (
        "情绪克制、压迫感适中，关键句力度加重"
        if any(word in role_text for word in ("反派", "冷峻", "威严"))
        else "整体情绪自然可信，表达有适度起伏但不过度夸张"
    )
    audible_keywords = ("音", "声", "嗓", "语速", "吐字", "口音", "情绪", "沙哑", "清亮", "低沉")
    audible_raw = raw if any(word in raw for word in audible_keywords) else ""
    parts = [
        f"{age_label}{gender_label}，{pitch}，音色{texture}",
        f"{pace}，吐字清晰",
        emotion,
    ]
    if audible_raw:
        parts.append(audible_raw[:240])
    return "；".join(parts) + "。"


__all__ = ["compile_character_voice_description", "compile_voice_instruction"]
