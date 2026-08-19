"""Normalize durable beat records into the MiniMax H3 dialogue contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_DIALOGUE_AUDIO_TYPES = {"dialogue", "character_dialogue", "对白", "台词"}


def _text(*values: Any) -> str:
    return next((text for value in values if (text := str(value or "").strip())), "")


def _audio_type(beat: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    return _text(config.get("audio_type"), beat.get("audio_type")).lower()


def h3_dialogue_text(
    beat: Mapping[str, Any], config: Mapping[str, Any] | None = None
) -> str:
    """Read dialogue without treating narration or silent content as spoken text."""
    settings = config or {}
    legacy = _text(beat.get("dialogue"), beat.get("line"), settings.get("dialogue"))
    if legacy:
        return legacy
    if _audio_type(beat, settings) in _DIALOGUE_AUDIO_TYPES:
        return _text(beat.get("narration_segment"), settings.get("narration_segment"))
    return ""


def h3_speaker_text(
    beat: Mapping[str, Any], config: Mapping[str, Any] | None = None
) -> str:
    settings = config or {}
    return _text(beat.get("speaker"), beat.get("character"), settings.get("speaker"))


def h3_tone_text(
    beat: Mapping[str, Any], config: Mapping[str, Any] | None = None
) -> str:
    settings = config or {}
    return _text(beat.get("tone"), beat.get("emotion"), settings.get("tone"))


def h3_dialogue_required(
    beat: Mapping[str, Any], config: Mapping[str, Any] | None = None
) -> bool:
    settings = config or {}
    for source in (settings, beat):
        if "dialogue_required" in source:
            value = source["dialogue_required"]
            if isinstance(value, str):
                return value.strip().lower() not in {"", "0", "false", "no", "否"}
            return bool(value)
    return bool(h3_dialogue_text(beat, settings)) or _audio_type(
        beat, settings
    ) in _DIALOGUE_AUDIO_TYPES


__all__ = [
    "h3_dialogue_required",
    "h3_dialogue_text",
    "h3_speaker_text",
    "h3_tone_text",
]
