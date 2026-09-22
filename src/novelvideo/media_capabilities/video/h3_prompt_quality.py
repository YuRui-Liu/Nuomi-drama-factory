"""Deterministic, pre-transport quality gate for MiniMax H3 director plans."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from .h3_director_plan import H3DirectorPlan
from .h3_prompt_compiler import has_unscoped_boundary_state
from .h3_rigid_prompt import H3_RIGID_SECTION_ORDER
from .h3_wire import (
    H3BaseWire,
    H3ReferenceWire,
    compile_h3_wire,
    inspect_h3_reference_semantics,
)
from .models import H3Mode

if TYPE_CHECKING:
    from .h3_prompt_optimizer import H3PromptContext
    from .h3_timeline import H3DirectorSegment


H3_PROMPT_QUALITY_VERSION = 9
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")
_WIRE_METADATA_FIELDS = frozenset({"mode", "duration_seconds", "final_shot_number"})
_BASE_WIRE_FIELD_ORDER = tuple(
    name for name in H3BaseWire.model_fields if name not in _WIRE_METADATA_FIELDS
)
_REFERENCE_WIRE_FIELD_ORDER = tuple(
    name for name in H3ReferenceWire.model_fields if name not in _WIRE_METADATA_FIELDS
)
_CANONICAL_WIRE_FIELDS = tuple(
    dict.fromkeys((*_BASE_WIRE_FIELD_ORDER, *_REFERENCE_WIRE_FIELD_ORDER))
)
_WIRE_FIELD_PATTERN = re.compile(
    rf"(?m)^({'|'.join(re.escape(field) for field in _CANONICAL_WIRE_FIELDS)}):"
)
_EVENT_TIMESTAMP_PATTERN = re.compile(r"\bAt (\d{2}):(\d{2})\.(\d{3}),")
_TIMED_ACTION_PATTERN = re.compile(r"^At \d{2}:\d{2}\.\d{3},")
_TIMED_CAMERA_PATTERN = re.compile(
    r"^At \d{2}:\d{2}\.\d{3},\s+(?:the\s+)?camera\b",
    re.IGNORECASE,
)
_DIALOGUE_PAYLOAD_PATTERN = re.compile(
    r"(?:<scenetrans>)?<d>\[[^\]\r\n]+\][^<\r\n]+</d>(?:<cutoff>)?"
)
_DIALOGUE_LINE_PATTERN = re.compile(
    r"\(S[1-9][0-9]*\).*:\s*"
    r"(?:<scenetrans>)?<d>\[[^\]\r\n]+\][^<\r\n]+</d>(?:<cutoff>)?"
)
_RETENTION_ITEM_PATTERN = re.compile(
    r"^-\s+(?P<subject><(?:Subject|Picture|Video|Audio) "
    r"[1-9][0-9]*>[^:]*):\s*(?P<retain>\S.*)$"
)
_ANGLE_TAG_PATTERN = re.compile(r"</?[^>\r\n]+>")
_ALLOWED_WIRE_TAG_PATTERN = re.compile(
    r"(?:<d>|</d>|<scenetrans>|<cutoff>|<Picture [1-9][0-9]*>|"
    r"<Subject [1-9][0-9]*>|<Video [1-9][0-9]*>|<Audio [1-9][0-9]*>)"
)
_ALIGNMENT_PREFIXES = (
    "For the target video, at 0.00 seconds into the target video, ",
    "How the reference pictures align with the target video — ",
)
_LEGACY_NO_MUSIC = frozenset(
    {"none", "none.", "no music", "no music.", "no music. sfx only."}
)
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
_PHYSICS_DIMENSION_PATTERNS = (
    re.compile(r"\b(?:weight|mass|gravity)\b|(?:重量|重力)", re.IGNORECASE),
    re.compile(
        r"\b(?:contact|support|shadow)\b|(?:接触|支撑|阴影)", re.IGNORECASE
    ),
    re.compile(
        r"\b(?:inertia|momentum|settle)\b|(?:惯性|动量|停稳)", re.IGNORECASE
    ),
)


class H3PromptQualityIssue(BaseModel):
    model_config = _MODEL_CONFIG
    code: str
    message: str
    field: str | None = None
    severity: Literal["error"] = "error"
    location: str | None = None

    @model_validator(mode="before")
    @classmethod
    def synchronize_field_and_legacy_location(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        field = data.get("field")
        location = data.get("location")
        if field is not None and location is not None and field != location:
            raise ValueError("field and location must match")
        synchronized = field if field is not None else location
        data["field"] = synchronized
        data["location"] = synchronized
        return data


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


def inspect_h3_prompt(
    prompt: str,
    mode: H3Mode | str,
    duration_seconds: float,
) -> H3PromptQualityReport:
    """Inspect a compiled official-wire prompt before transport."""
    issues: list[H3PromptQualityIssue] = []
    try:
        resolved_mode = H3Mode(mode)
    except ValueError:
        _add(issues, "h3.mode_invalid", "mode must be an official H3 mode", "mode")
        return H3PromptQualityReport(passed=False, issues=tuple(issues))

    fields, wire_format_valid = _extract_wire_fields(prompt, resolved_mode)
    actual_order = tuple(name for name, _ in fields)
    expected_order = (
        _REFERENCE_WIRE_FIELD_ORDER
        if resolved_mode is H3Mode.REF2VA
        else _BASE_WIRE_FIELD_ORDER
    )
    if sorted(actual_order) != sorted(expected_order):
        _add(
            issues,
            "h3.wire_field_set",
            "prompt fields must exactly match the mode-specific official wire",
            "prompt",
        )
    elif actual_order != expected_order:
        _add(
            issues,
            "h3.wire_field_order",
            "prompt fields must follow the official wire order",
            "prompt",
        )
    if not wire_format_valid:
        _add(
            issues,
            "h3.wire_format_invalid",
            "prompt must match the mode-specific official wire grammar",
            "prompt",
        )

    values_by_field: dict[str, list[str]] = {}
    for name, value in fields:
        values_by_field.setdefault(name, []).append(value)
    for field in expected_order:
        if field in values_by_field and any(
            not value.strip() for value in values_by_field[field]
        ):
            _add(
                issues,
                "h3.wire_field_empty",
                "official wire fields must not be empty",
                field,
            )

    _inspect_field_occurrences(actual_order, issues)
    duration = _validated_wire_duration(duration_seconds)
    if duration is None:
        _add(
            issues,
            "h3.duration_out_of_range",
            "duration_seconds must be between 4 and 15 seconds",
            "duration_seconds",
        )

    description_field = (
        "detailed_description"
        if resolved_mode is H3Mode.REF2VA
        else "integrated_multimodal_description"
    )
    description = _first_field_value(values_by_field, description_field)
    if duration is not None:
        _inspect_base_wire_contract(
            values_by_field,
            resolved_mode,
            duration,
            issues,
        )
        _inspect_alignment(prompt, description, resolved_mode, duration, issues)
        _inspect_event_timestamps(prompt, duration, issues)
        _inspect_action_budget(description, duration, issues)

    if resolved_mode is H3Mode.REF2VA:
        definitions = _first_field_value(values_by_field, "subject_definitions")
        retention = _first_field_value(values_by_field, "retention_analysis")
        retention_lines = tuple(
            line for line in retention.splitlines() if line.strip()
        )
        retention_matches = tuple(
            match
            for line in retention_lines
            if (match := _RETENTION_ITEM_PATTERN.match(line)) is not None
        )
        if not retention_lines or len(retention_matches) != len(retention_lines):
            _add(
                issues,
                "h3.retention_analysis_missing",
                "Ref2VA requires nonempty subject retention rules",
                "retention_analysis",
            )
        semantic_issues = inspect_h3_reference_semantics(
            definitions,
            tuple(
                (match.group("subject"), match.group("retain"))
                for match in retention_matches
            ),
            description,
            additional_text=(
                _first_field_value(values_by_field, "summary"),
                _first_field_value(values_by_field, "overall_soundscape"),
                _first_field_value(values_by_field, "non_diegetic_music"),
            ),
        )
        semantic_issue_details = {
            "reference_definition_invalid": (
                "h3.reference_definition_invalid",
                "subject definitions must be unique, continuous, and map source pictures",
                "subject_definitions",
            ),
            "reference_picture_out_of_range": (
                "h3.reference_picture_out_of_range",
                "every Picture tag must be declared by a subject definition",
                "detailed_description",
            ),
            "reference_relation_invalid": (
                "h3.reference_relation_invalid",
                "retention relations must use the official visual relation vocabulary",
                "retention_analysis",
            ),
            "reference_subject_mismatch": (
                "h3.reference_subject_mismatch",
                "subject definitions and retention analysis must match one-to-one",
                "retention_analysis",
            ),
            "reference_subject_inactive": (
                "h3.reference_subject_inactive",
                "every separately defined reference label must appear in detailed_description",
                "detailed_description",
            ),
            "reference_label_invalid": (
                "h3.reference_label_invalid",
                "reference labels must use complete official tags",
                "prompt",
            ),
            "reference_label_undefined": (
                "h3.reference_label_undefined",
                "every reference label must be declared in subject_definitions",
                "detailed_description",
            ),
        }
        for semantic_issue in semantic_issues:
            _add(issues, *semantic_issue_details[semantic_issue])

    _inspect_dialogue_and_markers(prompt, issues)
    _inspect_forbidden_legacy_wire(prompt, issues)
    music = _first_field_value(values_by_field, "non_diegetic_music")
    if " ".join(music.casefold().split()) in _LEGACY_NO_MUSIC:
        _add(
            issues,
            "h3.legacy_no_music_phrase",
            'no-music semantics must use canonical "N/A"',
            "non_diegetic_music",
        )
    return H3PromptQualityReport(passed=not issues, issues=tuple(issues))


def _extract_wire_fields(
    prompt: str,
    mode: H3Mode,
) -> tuple[tuple[tuple[str, str], ...], bool]:
    expected_order = (
        _REFERENCE_WIRE_FIELD_ORDER
        if mode is H3Mode.REF2VA
        else _BASE_WIRE_FIELD_ORDER
    )
    body = _wire_body(prompt, mode)
    actual_order = tuple(match.group(1) for match in _WIRE_FIELD_PATTERN.finditer(body))
    if actual_order != expected_order:
        return tuple((field, "") for field in actual_order), False

    fields: list[tuple[str, str]] = []
    cursor = 0
    for index, field in enumerate(expected_order):
        prefix = f"{field}:\n" if mode is H3Mode.REF2VA else f"{field}: "
        if not body.startswith(prefix, cursor):
            return tuple(fields), False
        value_start = cursor + len(prefix)
        if index + 1 < len(expected_order):
            next_field = expected_order[index + 1]
            delimiter = f"\n\n{next_field}:"
            value_end = body.find(delimiter, value_start)
            if value_end < 0:
                return tuple(fields), False
            value = body[value_start:value_end]
            cursor = value_end + 2
        else:
            value = body[value_start:]
            cursor = len(body)
        fields.append((field, value))
    format_valid = cursor == len(body) and all(
        value and value == value.strip() for _, value in fields
    )
    return tuple(fields), format_valid


def _wire_body(prompt: str, mode: H3Mode) -> str:
    if mode in {H3Mode.I2VA, H3Mode.FL2VA, H3Mode.L2VA} and prompt.startswith(
        _ALIGNMENT_PREFIXES
    ):
        _, separator, body = prompt.partition("\n\n")
        return body if separator else prompt
    return prompt


def _first_field_value(values: dict[str, list[str]], field: str) -> str:
    candidates = values.get(field, ())
    return candidates[0] if candidates else ""


def _inspect_field_occurrences(
    fields: tuple[str, ...],
    issues: list[H3PromptQualityIssue],
) -> None:
    if fields.count("overall_soundscape") != 1:
        _add(
            issues,
            "h3.soundscape_occurrence",
            "overall_soundscape must appear exactly once",
            "overall_soundscape",
        )
    if fields.count("non_diegetic_music") != 1:
        _add(
            issues,
            "h3.music_occurrence",
            "non_diegetic_music must appear exactly once",
            "non_diegetic_music",
        )


def _validated_wire_duration(duration_seconds: float) -> float | None:
    try:
        probe = H3BaseWire(
            mode=H3Mode.T2VA,
            duration_seconds=duration_seconds,
            integrated_multimodal_description="[Shot 1] duration validation.",
            overall_soundscape="validation",
            non_diegetic_music="N/A",
        )
    except ValidationError:
        return None
    return probe.duration_seconds


def _inspect_base_wire_contract(
    values: dict[str, list[str]],
    mode: H3Mode,
    duration_seconds: float,
    issues: list[H3PromptQualityIssue],
) -> None:
    if mode is H3Mode.REF2VA:
        return
    description = _first_field_value(values, "integrated_multimodal_description")
    soundscape = _first_field_value(values, "overall_soundscape")
    music = _first_field_value(values, "non_diegetic_music")
    if not all((description, soundscape, music)):
        return
    shot_numbers = tuple(
        int(number) for number in re.findall(r"\[Shot ([1-9][0-9]*)\]", description)
    )
    try:
        H3BaseWire(
            mode=mode,
            duration_seconds=duration_seconds,
            final_shot_number=shot_numbers[-1] if shot_numbers else 1,
            integrated_multimodal_description=description,
            overall_soundscape=soundscape,
            non_diegetic_music=music,
        )
    except ValidationError:
        _add(
            issues,
            "h3.shot_sequence_invalid",
            "base wire description must use the canonical ordered shot sequence",
            "integrated_multimodal_description",
        )


def _canonical_alignment(
    mode: H3Mode,
    duration_seconds: float,
    description: str,
) -> str:
    shot_numbers = tuple(
        int(number) for number in re.findall(r"\[Shot ([1-9][0-9]*)\]", description)
    )
    final_shot_number = shot_numbers[-1] if shot_numbers else 1
    probe_description = "\n".join(
        f"[Shot {number}] alignment validation."
        for number in range(1, final_shot_number + 1)
    )
    wire = H3BaseWire(
        mode=mode,
        duration_seconds=duration_seconds,
        final_shot_number=final_shot_number,
        integrated_multimodal_description=probe_description,
        overall_soundscape="validation",
        non_diegetic_music="N/A",
    )
    compiled = compile_h3_wire(wire)
    return compiled.split("\n\nintegrated_multimodal_description:", 1)[0]


def _inspect_alignment(
    prompt: str,
    description: str,
    mode: H3Mode,
    duration_seconds: float,
    issues: list[H3PromptQualityIssue],
) -> None:
    has_alignment_prefix = prompt.startswith(_ALIGNMENT_PREFIXES)
    if mode in {H3Mode.T2VA, H3Mode.REF2VA}:
        if has_alignment_prefix:
            _add(
                issues,
                "h3.frame_alignment_forbidden",
                f"{mode.value} forbids a frame-alignment prefix",
                "prompt",
            )
        return

    expected = _canonical_alignment(mode, duration_seconds, description)
    if mode is H3Mode.I2VA:
        if not prompt.startswith(f"{expected}\n\n"):
            _add(
                issues,
                "h3.first_image_alignment_missing",
                "I2VA requires the canonical Picture 1 first-frame alignment",
                "prompt",
            )
        return

    if mode is H3Mode.FL2VA:
        first_anchor = expected.split("; ", 1)[0]
        if not prompt.startswith(first_anchor):
            _add(
                issues,
                "h3.first_image_alignment_missing",
                "FL2VA requires Picture 1 at the first frame",
                "prompt",
            )
        if not prompt.startswith(f"{expected}\n\n"):
            _add(
                issues,
                "h3.last_image_alignment_missing",
                "FL2VA requires Picture 2 at the declared terminal time",
                "prompt",
            )
        return

    if not prompt.startswith(f"{expected}\n\n"):
        _add(
            issues,
            "h3.last_image_alignment_missing",
            "L2VA requires Picture 1 at the declared terminal time",
            "prompt",
        )


def _inspect_event_timestamps(
    prompt: str,
    duration_seconds: float,
    issues: list[H3PromptQualityIssue],
) -> None:
    timestamps = tuple(
        int(minutes) * 60 + int(seconds) + int(milliseconds) / 1000
        for minutes, seconds, milliseconds in _EVENT_TIMESTAMP_PATTERN.findall(prompt)
    )
    if any(timestamp > duration_seconds for timestamp in timestamps):
        _add(
            issues,
            "h3.event_timestamp_out_of_range",
            "event timestamps must not exceed the terminal video time",
            "prompt",
        )


def _inspect_action_budget(
    description: str,
    duration_seconds: float,
    issues: list[H3PromptQualityIssue],
) -> None:
    action_shots: set[str] = set()
    current_shot: str | None = None
    for line in description.splitlines():
        shot = re.match(r"^\[Shot ([1-9][0-9]*)\]", line)
        if shot is not None:
            current_shot = shot.group(1)
        if (
            current_shot is not None
            and _TIMED_ACTION_PATTERN.match(line)
            and _TIMED_CAMERA_PATTERN.match(line) is None
            and "<d>" not in line
            and "converges toward" not in line.casefold()
        ):
            action_shots.add(current_shot)
    action_beats = len(action_shots)
    maximum = 1 if duration_seconds <= 6 else 2 if duration_seconds <= 10 else 3
    if action_beats > maximum:
        _add(
            issues,
            "h3.action_beat_overload",
            f"{duration_seconds:g}s prompts allow at most {maximum} main action beats",
            "prompt",
        )


def _inspect_dialogue_and_markers(
    prompt: str,
    issues: list[H3PromptQualityIssue],
) -> None:
    tags = _ANGLE_TAG_PATTERN.findall(prompt)
    if any(_ALLOWED_WIRE_TAG_PATTERN.fullmatch(tag) is None for tag in tags):
        _add(
            issues,
            "h3.control_marker_invalid",
            "only official dialogue, scene-transition, and cutoff markers are allowed",
            "prompt",
        )

    payloads = tuple(_DIALOGUE_PAYLOAD_PATTERN.finditer(prompt))
    dialogue_lines = tuple(
        line for line in prompt.splitlines() if "<d>" in line or "</d>" in line
    )
    if (
        prompt.count("<d>") != prompt.count("</d>")
        or prompt.count("<d>") != len(payloads)
        or any(_DIALOGUE_LINE_PATTERN.search(line) is None for line in dialogue_lines)
    ):
        _add(
            issues,
            "h3.dialogue_wire_invalid",
            "dialogue requires (S1), language, text, and canonical d-tag wrapping",
            "prompt",
        )

    without_payloads = _DIALOGUE_PAYLOAD_PATTERN.sub("", prompt)
    if "<scenetrans>" in without_payloads or "<cutoff>" in without_payloads:
        _add(
            issues,
            "h3.control_marker_invalid",
            "scenetrans and cutoff markers must be attached to dialogue",
            "prompt",
        )


def _inspect_forbidden_legacy_wire(
    prompt: str,
    issues: list[H3PromptQualityIssue],
) -> None:
    if re.search(r"(?mi)^\s*mode\s*:", prompt):
        _add(
            issues,
            "h3.mode_prefix_forbidden",
            "the official wire does not include a mode prefix",
            "prompt",
        )
    raw_dialogue = re.search(r"(?mi)^\s*dialogue\s*:", prompt) is not None
    raw_dialogue = raw_dialogue or any(
        re.search(r"\b(?:says|asks|shouts|whispers)\s*:", line, re.IGNORECASE)
        and "<d>" not in line
        for line in prompt.splitlines()
    )
    if raw_dialogue:
        _add(
            issues,
            "h3.raw_dialogue_forbidden",
            "dialogue text must use canonical d-tag wrapping",
            "prompt",
        )
    if any(
        re.search(rf"(?mi)^\s*{re.escape(heading)}\s*:?(?:\s|$)", prompt)
        for heading in H3_RIGID_SECTION_ORDER
    ):
        _add(
            issues,
            "h3.internal_heading_leaked",
            "internal rigid-plan section headings must not leak into the wire prompt",
            "prompt",
        )


def inspect_h3_plan(
    plan: H3DirectorPlan,
    *,
    segment: H3DirectorSegment | None = None,
    context: H3PromptContext | None = None,
) -> H3PromptQualityReport:
    """Inspect semantics that are stricter than the structural schema."""
    issues: list[H3PromptQualityIssue] = []
    first_action = plan.shots[0].actions[0]
    for index, lock in enumerate(plan.continuity_locks):
        if has_unscoped_boundary_state(lock):
            _add(
                issues, "global_state_scope",
                "Place source boundary states in the scoped ACTION timeline, "
                "not in global continuity locks.",
                f"continuity_locks.{index}",
            )
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
        planned_cues = tuple(cue for shot in plan.shots for cue in shot.dialogue)
        if segment.dialogue_lines:
            if len(planned_cues) != len(segment.dialogue_lines) or any(
                cue.text != line.text
                for cue, line in zip(
                    planned_cues, segment.dialogue_lines, strict=False
                )
            ):
                _add(
                    issues,
                    "dialogue_not_verbatim",
                    "each director cue must preserve its source line in order",
                    "shots.dialogue",
                )
            for cue_index, (cue, line) in enumerate(
                zip(planned_cues, segment.dialogue_lines, strict=False)
            ):
                if cue.speaker != line.speaker:
                    _add(
                        issues,
                        "dialogue_speaker_mismatch",
                        "each dialogue cue speaker must match its source line",
                        f"shots.dialogue.{cue_index}.speaker",
                    )
                if line.tone and cue.delivery != line.tone:
                    _add(
                        issues,
                        "dialogue_tone_mismatch",
                        "each dialogue cue delivery must match its source tone",
                        f"shots.dialogue.{cue_index}.delivery",
                    )
        else:
            planned_dialogue = "".join(cue.text for cue in planned_cues)
            if planned_dialogue != segment.dialogue:
                _add(issues, "dialogue_not_verbatim", "director plan dialogue must preserve source text verbatim", "shots.dialogue")

    if context is not None:
        if not context.first_frame_sha256:
            _add(issues, "first_frame_anchor", "Picture 1 hash is required", "context.first_frame_sha256")
        if plan.mode is H3Mode.FL2VA and not context.last_frame_sha256:
            _add(issues, "last_frame_anchor", "Picture 2 hash is required for FL2VA", "context.last_frame_sha256")
        if plan.schema_version < 2 or plan.rigid_prompt is None:
            _add(
                issues,
                "rigid_prompt_required",
                "paid H3 generation requires a schema_version>=2 rigid prompt",
                "rigid_prompt",
            )

    if segment is not None and context is not None:
        for shot_index, shot in enumerate(plan.shots):
            for cue_index, cue in enumerate(shot.dialogue):
                if not segment.dialogue_lines and cue.speaker != segment.speaker:
                    _add(
                        issues,
                        "dialogue_speaker_mismatch",
                        "dialogue cue speaker must match the source segment speaker",
                        f"shots.{shot_index}.dialogue.{cue_index}.speaker",
                    )
            source_lines = (
                tuple(line.text for line in segment.dialogue_lines)
                if segment.dialogue_lines
                else ((segment.dialogue,) if segment.dialogue else ())
            )
            if source_lines:
                for action_index, action in enumerate(shot.actions):
                    if any(line in action.description for line in source_lines):
                        _add(
                            issues,
                            "dialogue_in_action_timing",
                            "source dialogue belongs only in AUDIO dialogue cues",
                            f"shots.{shot_index}.actions.{action_index}.description",
                        )

    if plan.rigid_prompt is not None:
        _inspect_rigid_prompt(plan, issues, context=context)

    return H3PromptQualityReport(passed=not issues, issues=tuple(issues))


def normalize_h3_action_timeline(plan: H3DirectorPlan) -> H3DirectorPlan:
    """Close mechanical action gaps while preserving semantic action order."""
    normalized_shots = []
    for shot in plan.shots:
        expected_frame = shot.start_frame
        normalized_actions = []
        last_index = len(shot.actions) - 1
        for action_index, action in enumerate(shot.actions):
            end_frame = (
                shot.end_frame if action_index == last_index else action.end_frame
            )
            if end_frame <= expected_frame or end_frame > shot.end_frame:
                return plan
            normalized_actions.append(
                action.model_copy(
                    update={
                        "start_frame": expected_frame,
                        "end_frame": end_frame,
                    }
                )
            )
            expected_frame = end_frame
        normalized_shots.append(
            shot.model_copy(update={"actions": tuple(normalized_actions)})
        )
    return plan.model_copy(update={"shots": tuple(normalized_shots)})


def _inspect_rigid_prompt(
    plan: H3DirectorPlan,
    issues: list[H3PromptQualityIssue],
    *,
    context: H3PromptContext | None,
) -> None:
    rigid = plan.rigid_prompt
    if rigid is None:
        return

    active = rigid.scene_context.active_characters
    active_set = set(active)
    context_ids = context.active_character_ids if context is not None else ()
    context_active = set(context_ids)
    if (
        rigid.scene_context.exact_character_count != len(active)
        or (context_active and active_set != context_active)
    ):
        _add(
            issues,
            "character_count_mismatch",
            "exact character count and active character IDs must agree",
            "rigid_prompt.scene_context",
        )
    reference_tags = tuple(reference.tag for reference in rigid.active_references)
    resolved_tags = context.resolved_reference_tags if context is not None else ()
    resolved_facts = context.resolved_references if context is not None else ()
    if (
        len(active) != len(active_set)
        or len(reference_tags) != len(set(reference_tags))
        or len(context_ids) != len(context_active)
        or len(resolved_tags) != len(set(resolved_tags))
    ):
        _add(
            issues,
            "duplicate_active_reference",
            "active characters and reference tags must be unique",
            "rigid_prompt.active_references",
        )
    if reference_tags:
        resolved = {fact.tag for fact in resolved_facts}
        if any(tag not in resolved for tag in reference_tags):
            _add(
                issues,
                "unresolved_active_reference",
                "every active reference must resolve to a supplied asset tag",
                "rigid_prompt.active_references",
            )
        facts_by_tag = {fact.tag: fact for fact in resolved_facts}
        for index, reference in enumerate(rigid.active_references):
            fact = facts_by_tag.get(reference.tag)
            if fact is None:
                continue
            if fact.kind not in {"character", "location"}:
                _add(
                    issues,
                    "unsupported_active_reference_kind",
                    "prop and temporary references cannot be active references",
                    f"rigid_prompt.active_references.{index}.kind",
                )
            elif reference.kind != fact.kind:
                _add(
                    issues,
                    "reference_kind_mismatch",
                    "active reference kind must match its resolved reference fact",
                    f"rigid_prompt.active_references.{index}.kind",
                )

    if not rigid.location_map.landmarks:
        _add(
            issues,
            "location_landmarks_required",
            "location map requires at least one stable landmark",
            "rigid_prompt.location_map.landmarks",
        )

    expected_shots = tuple(shot.shot_id for shot in plan.shots)
    blocking_by_shot = {
        blocking.shot_id: blocking for blocking in rigid.spatial_blocking
    }
    blocking_ids = tuple(blocking.shot_id for blocking in rigid.spatial_blocking)
    if blocking_ids != expected_shots or len(blocking_ids) != len(set(blocking_ids)):
        _add(
            issues,
            "first_frame_character_missing",
            "spatial blocking must contain exactly one entry for every shot in order",
            "rigid_prompt.spatial_blocking",
        )
    for shot_id in expected_shots:
        blocking = blocking_by_shot.get(shot_id)
        subject_ids = (
            tuple(subject.character_id for subject in blocking.subjects)
            if blocking is not None
            else ()
        )
        subject_set = set(subject_ids)
        if blocking is None or not active_set.issubset(subject_set):
            _add(
                issues,
                "first_frame_character_missing",
                "every active character needs one frame-zero blocking record per shot",
                f"rigid_prompt.spatial_blocking.{shot_id}",
            )
        if (
            blocking is None
            or subject_set != active_set
            or len(subject_ids) != len(subject_set)
        ):
            _add(
                issues,
                "first_frame_character_mismatch",
                "frame-zero blocking characters must exactly match active characters",
                f"rigid_prompt.spatial_blocking.{shot_id}.subjects",
            )

    expected_duration = plan.total_frames / plan.fps
    if not math.isclose(
        rigid.format_mode.total_duration_seconds,
        expected_duration,
        rel_tol=0.0,
        abs_tol=1e-6,
    ):
        _add(
            issues,
            "format_duration_mismatch",
            "format duration must equal total_frames divided by fps",
            "rigid_prompt.format_mode.total_duration_seconds",
        )
    if rigid.format_mode.mode == "hard_cuts":
        expected_cut_points = tuple(
            shot.start_frame / plan.fps for shot in plan.shots[1:]
        )
        actual_cut_points = rigid.format_mode.cut_points_seconds
        tolerance = 1 / plan.fps
        if len(actual_cut_points) != len(expected_cut_points) or any(
            abs(actual - expected) > tolerance
            for actual, expected in zip(
                actual_cut_points, expected_cut_points, strict=True
            )
        ):
            _add(
                issues,
                "format_cut_points_mismatch",
                "hard-cut points must match shot boundaries within one frame",
                "rigid_prompt.format_mode.cut_points_seconds",
            )
    elif len(plan.shots) != 1:
        _add(
            issues,
            "format_mode_mismatch",
            "single-take format requires exactly one director shot",
            "rigid_prompt.format_mode.mode",
        )

    optics_ids = tuple(optics.shot_id for optics in rigid.optics)
    if optics_ids != expected_shots or len(optics_ids) != len(set(optics_ids)):
        _add(
            issues,
            "optics_shot_mismatch",
            "optics must contain exactly one entry for every shot in order",
            "rigid_prompt.optics",
        )

    if context is not None and context.lighting_facts_json:
        facts = json.loads(context.lighting_facts_json)
        aliases = {
            "source_logic": ("source_logic",),
            "primary_source": ("primary_source", "key_source"),
            "origin": ("origin",),
            "direction": ("direction",),
            "shadow_direction": ("shadow_direction",),
            "continuity_key": ("continuity_key",),
            "color": ("color", "color_temperature"),
            "environment_effect": (
                "environment_effect",
                "exposure_priority",
            ),
        }
        for field, names in aliases.items():
            expected = {
                _normalized_fact(value)
                for value in _values_for_keys(facts, names)
                if isinstance(value, str) and value.strip()
            }
            actual = _normalized_fact(getattr(rigid.lighting, field))
            if expected and (len(expected) != 1 or actual not in expected):
                _add(
                    issues,
                    "lighting_source_conflict",
                    f"lighting {field} conflicts with continuity facts",
                    f"rigid_prompt.lighting.{field}",
                )

    acting_ids = tuple(item.character_id for item in rigid.character_acting)
    if set(acting_ids) != active_set or len(acting_ids) != len(set(acting_ids)):
        _add(
            issues,
            "character_acting_missing",
            "every active character needs exactly one acting plan",
            "rigid_prompt.character_acting",
        )

    if context is not None and context.style_prefix:
        if rigid.style_prefix != context.style_prefix:
            _add(
                issues,
                "style_prefix_mismatch",
                "rigid prompt must preserve the project Style Prefix verbatim",
                "rigid_prompt.style_prefix",
            )

    action_entities: set[str] = set()
    for shot_index, shot in enumerate(plan.shots):
        for action_index, action in enumerate(shot.actions):
            action_entities.update(action.moving_entities)
            if (
                action.phase != "establish"
                and action.change_domain == "subject_or_prop"
                and not action.moving_entities
            ):
                _add(
                    issues,
                    "action_moving_entities_required",
                    "subject or prop actions must identify their moving entities",
                    f"shots.{shot_index}.actions.{action_index}.moving_entities",
                )
    if action_entities != set(rigid.physics.moving_entities):
        _add(
            issues,
            "physics_entity_mismatch",
            "ACTION and PHYSICS moving entity sets must match exactly",
            "rigid_prompt.physics.moving_entities",
        )

    if rigid.physics.moving_entities and not rigid.physics.statements:
        _add(
            issues,
            "physics_required",
            "moving entities require observable physics statements",
            "rigid_prompt.physics.statements",
        )
    elif rigid.physics.moving_entities:
        physics = " ".join(rigid.physics.statements)
        if any(
            pattern.search(physics) is None
            for pattern in _PHYSICS_DIMENSION_PATTERNS
        ):
            _add(
                issues,
                "physics_incomplete",
                "physics must cover weight, contact/support, and inertia/momentum",
                "rigid_prompt.physics.statements",
            )
    if not rigid.quality.requirements:
        _add(
            issues,
            "quality_requirements_required",
            "rigid prompt requires explicit quality requirements",
            "rigid_prompt.quality.requirements",
        )
    visible_props = {
        prop
        for blocking in rigid.spatial_blocking
        for subject in blocking.subjects
        for prop in subject.held_props
    }
    allowed_characters = context_active if context_active else active_set
    allowed_moving_entities = allowed_characters | visible_props
    all_moving_entities = action_entities | set(rigid.physics.moving_entities)
    if not all_moving_entities.issubset(allowed_moving_entities):
        _add(
            issues,
            "unknown_moving_entity",
            "moving entities must be active characters or visible held props",
            "rigid_prompt.physics.moving_entities",
        )
    physics_text = " ".join(rigid.physics.statements)
    if any(
        not _physics_names_entity(physics_text, entity)
        for entity in all_moving_entities
    ):
        _add(
            issues,
            "physics_entity_description_missing",
            "physics statements must explicitly name every moving entity",
            "rigid_prompt.physics.statements",
        )
    required_counts = {
        "characters": rigid.scene_context.exact_character_count,
        **({"references": len(rigid.active_references)} if reference_tags else {}),
        **({"props": len(visible_props)} if visible_props else {}),
    }
    for target, expected_count in required_counts.items():
        targeted = tuple(
            constraint
            for constraint in rigid.positive_constraints
            if constraint.target == target
        )
        if not targeted:
            _add(
                issues,
                "positive_constraints_required",
                f"positive constraints require a {target} target",
                f"rigid_prompt.positive_constraints.{target}",
            )
        elif not any(
            constraint.count == expected_count for constraint in targeted
        ):
            _add(
                issues,
                "positive_constraint_count_mismatch",
                f"positive constraint {target} count must equal {expected_count}",
                f"rigid_prompt.positive_constraints.{target}",
            )


def _values_for_keys(value: object, keys: tuple[str, ...]) -> tuple[object, ...]:
    values: list[object] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in keys:
                values.append(item)
            elif isinstance(item, (Mapping, list, tuple)):
                values.extend(_values_for_keys(item, keys))
    elif isinstance(value, (list, tuple)):
        for item in value:
            values.extend(_values_for_keys(item, keys))
    return tuple(values)


def _normalized_fact(value: str) -> str:
    return " ".join(value.casefold().split())


def _physics_names_entity(statements: str, entity: str) -> bool:
    escaped = re.escape(entity.casefold())
    if entity.isascii():
        pattern = rf"(?<![a-z0-9_.-]){escaped}(?![a-z0-9_.-])"
        return re.search(pattern, statements.casefold()) is not None
    return entity.casefold() in statements.casefold()


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
    has_detailed_multistep_blocking = _has_detailed_multistep_blocking(description)
    if phase in {"settle", "end_lock"}:
        return has_visible_end_state or has_detailed_multistep_blocking
    return (
        has_pacing_or_effort and has_visible_end_state
    ) or has_detailed_multistep_blocking


def _has_detailed_multistep_blocking(description: str) -> bool:
    """Accept concrete director blocking without requiring magic vocabulary.

    H3 plans commonly express effort, pacing, and the resulting pose as a
    sequence of clauses instead of using the small canonical word lists above.
    A sufficiently detailed multi-clause action is therefore valid while short
    one-step instructions such as ``He walks forward`` remain rejected.
    """
    latin_words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", description)
    cjk_characters = re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", description)
    if len(latin_words) + len(cjk_characters) < 14:
        return False
    clause_boundaries = sum(description.count(mark) for mark in (",", ";", "，", "；"))
    clause_boundaries += len(
        re.findall(
            r"\b(?:after|before|then|while|until|as|with)\b|"
            r"(?:随后|然后|同时|直到|随着|最终)",
            description,
            re.IGNORECASE,
        )
    )
    return clause_boundaries >= 2


__all__ = [
    "H3_PROMPT_QUALITY_VERSION",
    "H3PromptQualityError",
    "H3PromptQualityIssue",
    "H3PromptQualityReport",
    "inspect_h3_plan",
    "inspect_h3_prompt",
    "normalize_h3_action_timeline",
]
