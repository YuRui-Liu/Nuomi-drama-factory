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

    issues.extend(_duration_issues(prompt, spec, duration))
    issues.extend(_frame_mode_issues(prompt, spec))
    return issues


_DURATION_MENTION = re.compile(
    r"(?P<prefix>第\s*)?(?<!\d)(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?:秒|seconds?|secs?|s(?![a-z]))",
    re.IGNORECASE,
)
_LAST_FRAME_MENTIONS = (
    re.compile(r"\blast\s+frame\b", re.IGNORECASE),
    re.compile(r"尾帧"),
    re.compile(r"末帧"),
)
_FIRST_FRAME_ONLY_MENTIONS = (
    re.compile(r"\bonly\s+(?:the\s+)?first\s+frame\b", re.IGNORECASE),
    re.compile(r"\bfirst\s+frame\s+only\b", re.IGNORECASE),
    re.compile(r"(?:只|仅)(?:用|使用)?首帧"),
)


def _expected_duration(spec: dict, duration: float) -> float:
    for key in ("duration", "duration_seconds"):
        value = spec.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return float(duration)


def _duration_issues(prompt: str, spec: dict, duration: float) -> list[dict]:
    expected = _expected_duration(spec, duration)
    issues: list[dict] = []
    seen: set[float] = set()
    for match in _DURATION_MENTION.finditer(prompt):
        # 第 N 秒是时间点，不是段时长。
        if match.group("prefix"):
            continue
        value = float(match.group("value"))
        if value in seen:
            continue
        seen.add(value)
        if abs(value - expected) > 1e-6:
            issues.append(
                {
                    "code": "prompt_spec_conflict",
                    "severity": "error",
                    "message": (
                        f"prompt duration {value:g}s contradicts "
                        f"spec duration {expected:g}s"
                    ),
                }
            )
    return issues


def _frame_mode_issues(prompt: str, spec: dict) -> list[dict]:
    frame_mode = spec.get("frame_mode")
    if frame_mode == "first" and any(
        pattern.search(prompt) for pattern in _LAST_FRAME_MENTIONS
    ):
        return [
            {
                "code": "prompt_spec_conflict",
                "severity": "error",
                "message": "prompt references a last frame but spec is first-frame mode",
            }
        ]
    if frame_mode == "first_last" and any(
        pattern.search(prompt) for pattern in _FIRST_FRAME_ONLY_MENTIONS
    ):
        return [
            {
                "code": "prompt_spec_conflict",
                "severity": "error",
                "message": "prompt forces a first-frame-only read but spec locks first and last frames",
            }
        ]
    return []


__all__ = ["crosscheck_prompt"]
