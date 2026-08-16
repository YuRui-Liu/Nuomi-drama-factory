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


__all__ = ["compile_voice_instruction"]
