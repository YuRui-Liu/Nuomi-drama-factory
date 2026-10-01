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
    if re.search(r"\bhigh\b|高音|高亢", pitch) and re.search(r"\blow\b|低音|低沉", pitch):
        raise VoiceSpecError("pitch is contradictory")
    pace = _clean(spec.pace).lower()
    if re.search(r"\bfast\b|快速|很快", pace) and re.search(r"\bslow\b|缓慢|很慢", pace):
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
    """Compatibility adapter; use the same VoiceSpec compiler as the pipeline."""
    raw = _clean(raw_description)
    _validate(VoiceSpec(texture=raw))
    audible = "；".join(
        part.strip() for part in re.split(r"[。；;！!？?\n]", raw)
        if re.search(r"音色|声线|嗓音|声音|语速|吐字|口音|沙哑|清亮|低沉", part)
        and not re.search(r"身穿|出生|使命|不说人话|不会说话", part)
    )
    label = AGE_LABELS[normalize_voice_age(age_group)]
    gender_label = GENDER_LABELS.get(gender.strip().lower(), "")
    return compile_voice_instruction(VoiceSpec(
        age_impression=label + gender_label,
        pitch="" if audible else "中低音",
        texture=audible or "自然",
        articulation="吐字清晰" if not audible else "",
        pace="节奏自然" if not audible else "",
    ))


AGE_LABELS = {"": "", "child": "儿童", "teen": "青少年", "youth": "青年", "middle": "中年", "elder": "老年"}
AGE_ALIASES = {
    "unknown": "", "未知": "", "young": "youth", "young adult": "youth",
    "儿童": "child", "幼年": "child", "少年": "teen", "青少年": "teen",
    "青年": "youth", "中年": "middle", "middle-aged": "middle", "老年": "elder", "elderly": "elder",
}
GENDER_LABELS = {"female": "女性", "女": "女性", "woman": "女性", "male": "男性", "男": "男性", "man": "男性"}


def normalize_voice_age(value: str) -> str:
    value = str(value or "").strip().lower()
    value = AGE_ALIASES.get(value, value)
    if value not in AGE_LABELS:
        raise VoiceSpecError(f"invalid voice age: {value}")
    return value


def prepare_character_voice_request(*args, **kwargs):
    from novelvideo.media_capabilities.tts.character_voice import prepare_character_voice_request as prepare

    return prepare(*args, **kwargs)


__all__ = ["compile_character_voice_description", "compile_voice_instruction"]
