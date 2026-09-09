"""Canonical production workflow slot identifiers."""

from __future__ import annotations

import unicodedata


def _required(value: str, field_name: str) -> str:
    text = str(value)
    if not text.strip():
        raise ValueError(f"{field_name} must not be empty")
    if ":" in text:
        raise ValueError(f"{field_name} must not contain a colon")
    if any(unicodedata.category(character) == "Cc" for character in text):
        raise ValueError(f"{field_name} must not contain control characters")
    return text


def character_state_slot_id(character_name: str, identity_id: str) -> str:
    return (
        f"character:{_required(character_name, 'character_name')}:state:"
        f"{_required(identity_id, 'identity_id')}"
    )


def character_portrait_slot_id(character_name: str) -> str:
    return f"character:{_required(character_name, 'character_name')}:portrait"


def scene_base_slot_id(scene_name: str, kind: str) -> str:
    return (
        f"scene:{_required(scene_name, 'scene_name')}:base:"
        f"{_required(kind, 'kind')}"
    )


def scene_state_slot_id(base_scene_id: str, state_id: str, kind: str) -> str:
    return (
        f"scene:{_required(base_scene_id, 'base_scene_id')}:state:"
        f"{_required(state_id, 'state_id')}:{_required(kind, 'kind')}"
    )


def prop_reference_slot_id(prop_name: str) -> str:
    return f"prop:{_required(prop_name, 'prop_name')}:reference"
