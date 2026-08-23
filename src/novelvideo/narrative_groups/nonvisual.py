"""Shared classification for beats that contain production notes, not visuals."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from novelvideo.media_capabilities.video.h3_beat_adapter import h3_dialogue_text


_PRODUCTION_NOTE_MARKERS = (
    "制作说明",
    "无可直接拍摄",
    "无可拍摄",
    "时长信息卡",
)
_RELEVANT_FIELDS = (
    "dialogue",
    "line",
    "audio_type",
    "narration_segment",
    "narration",
    "visual_description",
    "shot_description",
    "description",
    "content",
)


def _beat_mapping(beat: Any) -> Mapping[str, Any]:
    if isinstance(beat, Mapping):
        return beat
    return {name: getattr(beat, name, None) for name in _RELEVANT_FIELDS}


def is_nonvisual_production_note(beat: Any) -> bool:
    """Return whether a silent beat is only a non-shootable production note."""
    data = _beat_mapping(beat)
    if h3_dialogue_text(data).strip() or str(data.get("narration") or "").strip():
        return False
    text = " ".join(
        str(data.get(name) or "").strip()
        for name in (
            "visual_description",
            "shot_description",
            "description",
            "content",
        )
    )
    return any(marker in text for marker in _PRODUCTION_NOTE_MARKERS)


__all__ = ["is_nonvisual_production_note"]
