"""Deterministic crosscheck of an H3 prompt against its panel spec and dialogue.

This is a contradiction detector, not a semantic grader: it only reports
verbatim dialogue that went missing and camera-side wording that contradicts
the panel spec. Callers record prompt_spec_conflict and block preview/submit.
"""

from __future__ import annotations

import re

_SIDE_PAIRS = (("左侧", "右侧"), ("left", "right"))
_ASCII_WORD = re.compile(r"^[a-z]+$")


def _mentions(text: str, word: str) -> bool:
    if not word:
        return False
    if _ASCII_WORD.fullmatch(word):
        pattern = rf"(?<![a-z]){re.escape(word)}(?![a-z])"
        return re.search(pattern, text.lower()) is not None
    return word in text


def _dialogue_text(line) -> str:
    if isinstance(line, dict):
        return str(line.get("text") or "")
    return str(line or "")


def _camera_side(spec: dict) -> str:
    side = spec.get("camera_side")
    if side:
        return str(side)
    sections = spec.get("sections") or {}
    return str(sections.get("axis") or "")


def crosscheck_prompt(*, prompt: str, spec: dict, duration: float) -> list[dict]:
    """Return prompt_spec_conflict issues; duration is kept for callers that
    crosscheck aligned segment timing in the same pass."""
    issues: list[dict] = []
    for line in spec.get("dialogue") or []:
        dialogue = _dialogue_text(line)
        if dialogue and dialogue not in prompt:
            issues.append(
                {
                    "code": "prompt_spec_conflict",
                    "severity": "error",
                    "message": f"dialogue missing verbatim: {dialogue}",
                }
            )

    side = _camera_side(spec)
    for left, right in _SIDE_PAIRS:
        if _mentions(side, left) and _mentions(prompt, right) and not _mentions(prompt, left):
            issues.append(
                {
                    "code": "prompt_spec_conflict",
                    "severity": "error",
                    "message": "camera side contradicts panel spec",
                }
            )
    return issues


__all__ = ["crosscheck_prompt"]
