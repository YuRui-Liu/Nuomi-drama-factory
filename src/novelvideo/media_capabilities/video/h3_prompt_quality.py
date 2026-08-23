"""Deterministic, pre-transport quality gate for MiniMax H3 director plans."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from .h3_director_plan import H3DirectorPlan
from .models import H3Mode

if TYPE_CHECKING:
    from .h3_prompt_optimizer import H3PromptContext
    from .h3_timeline import H3DirectorSegment


H3_PROMPT_QUALITY_VERSION = 1
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")
_VAGUE_ACTION_PATTERNS = (
    re.compile(r"\bmoves? naturally\b", re.IGNORECASE),
    re.compile(r"\bcamera (?:slowly )?moves?\b", re.IGNORECASE),
    re.compile(r"\bacts? naturally\b", re.IGNORECASE),
    re.compile(r"\breacts? naturally\b", re.IGNORECASE),
    re.compile(r"\bsome movement\b", re.IGNORECASE),
)
_ACTION_PACING_OR_EFFORT_PATTERNS = (
    re.compile(
        r"\b(?:slowly|quickly|rapidly|steadily|abruptly|cautiously|deliberately|"
        r"gently|sharply|forcefully|firmly)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:at a measured pace|in one swift motion|with controlled force)\b", re.IGNORECASE),
    re.compile(r"\b(?:brace|braces|braced|grip|grips|gripped|clench|clenches|slam|slams)\b", re.IGNORECASE),
)
_VISIBLE_END_STATE_PATTERNS = (
    re.compile(
        r"\b(?:stop|stops|stopping|settle|settles|hold|holds|remain|remains|"
        r"brace|braces|braced|grip|grips|gripped|open|opens|close|closes|"
        r"lock|locks|plant|plants|press|presses)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:ending|ends) with\b", re.IGNORECASE),
    re.compile(r"\b(?:flat against|wrapped around|fixed on)\b", re.IGNORECASE),
)
_PHASE_ORDER = {
    "establish": 0,
    "prepare": 1,
    "execute": 2,
    "react": 3,
    "settle": 4,
    "end_lock": 5,
}


class H3PromptQualityIssue(BaseModel):
    model_config = _MODEL_CONFIG
    code: str
    message: str
    severity: Literal["error"] = "error"
    location: str | None = None


class H3PromptQualityReport(BaseModel):
    model_config = _MODEL_CONFIG
    passed: bool
    issues: tuple[H3PromptQualityIssue, ...] = ()
    version: int = H3_PROMPT_QUALITY_VERSION

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)

    def raise_for_failure(self) -> None:
        if not self.passed:
            raise H3PromptQualityError(self)


class H3PromptQualityError(RuntimeError):
    """A generated plan failed before any paid video transport is called."""

    def __init__(self, report: H3PromptQualityReport):
        self.report = report
        summary = ", ".join(report.codes) or "unknown_quality_failure"
        super().__init__(f"H3 director plan failed quality gate: {summary}")


def inspect_h3_plan(
    plan: H3DirectorPlan,
    *,
    segment: H3DirectorSegment | None = None,
    context: H3PromptContext | None = None,
) -> H3PromptQualityReport:
    """Inspect semantics that are stricter than the structural schema."""
    issues: list[H3PromptQualityIssue] = []
    first_action = plan.shots[0].actions[0]
    if first_action.start_frame != 0 or first_action.phase != "establish":
        _add(issues, "first_frame_anchor", "first action must establish Picture 1 at frame 0", "shots.0.actions.0")

    for shot_index, shot in enumerate(plan.shots):
        expected_frame = shot.start_frame
        previous_phase = -1
        for action_index, action in enumerate(shot.actions):
            location = f"shots.{shot_index}.actions.{action_index}"
            if action.start_frame != expected_frame:
                _add(issues, "action_timeline_gap", "actions must cover each shot without gaps", location)
            expected_frame = action.end_frame
            phase_order = _PHASE_ORDER[action.phase]
            if phase_order < previous_phase:
                _add(issues, "action_phase_regression", "action phases must progress toward the terminal composition", location)
            previous_phase = phase_order
            if any(pattern.search(action.description) for pattern in _VAGUE_ACTION_PATTERNS):
                _add(issues, "vague_action", "action must state concrete motion and a visible result", location)
            if action.phase != "establish" and not _has_complete_action_detail(
                action.description, phase=action.phase
            ):
                _add(
                    issues,
                    "incomplete_action_detail",
                    "action must include pacing or physical effort and a visible end state",
                    location,
                )
        if expected_frame != shot.end_frame:
            _add(issues, "action_timeline_gap", "actions must reach the end of the shot", f"shots.{shot_index}.actions")

    if plan.mode is H3Mode.FL2VA and plan.shots[0].actions[-1].phase not in {"settle", "end_lock"}:
        _add(issues, "last_frame_anchor", "FL2VA must settle into Picture 2 at the terminal frame", "shots.0.actions")

    if segment is not None:
        expected_frames = round(segment.duration_seconds * plan.fps)
        if plan.total_frames != expected_frames:
            _add(issues, "duration_frame_mismatch", f"plan has {plan.total_frames} frames; segment requires {expected_frames}", "total_frames")
        planned_dialogue = "".join(cue.text for shot in plan.shots for cue in shot.dialogue)
        if planned_dialogue != segment.dialogue:
            _add(issues, "dialogue_not_verbatim", "director plan dialogue must preserve source text verbatim", "shots.dialogue")

    if context is not None:
        if not context.first_frame_sha256:
            _add(issues, "first_frame_anchor", "Picture 1 hash is required", "context.first_frame_sha256")
        if plan.mode is H3Mode.FL2VA and not context.last_frame_sha256:
            _add(issues, "last_frame_anchor", "Picture 2 hash is required for FL2VA", "context.last_frame_sha256")

    return H3PromptQualityReport(passed=not issues, issues=tuple(issues))


def _add(issues: list[H3PromptQualityIssue], code: str, message: str, location: str) -> None:
    issue = H3PromptQualityIssue(code=code, message=message, location=location)
    if issue not in issues:
        issues.append(issue)


def _has_complete_action_detail(description: str, *, phase: str) -> bool:
    has_pacing_or_effort = any(
        pattern.search(description) for pattern in _ACTION_PACING_OR_EFFORT_PATTERNS
    )
    has_visible_end_state = any(
        pattern.search(description) for pattern in _VISIBLE_END_STATE_PATTERNS
    )
    if phase in {"settle", "end_lock"}:
        return has_visible_end_state
    return has_pacing_or_effort and has_visible_end_state


__all__ = [
    "H3_PROMPT_QUALITY_VERSION",
    "H3PromptQualityError",
    "H3PromptQualityIssue",
    "H3PromptQualityReport",
    "inspect_h3_plan",
]
