"""Deterministic, pre-transport quality gate for MiniMax H3 director plans."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from .h3_director_plan import H3DirectorPlan
from .models import H3Mode

if TYPE_CHECKING:
    from .h3_prompt_optimizer import H3PromptContext
    from .h3_timeline import H3DirectorSegment


H3_PROMPT_QUALITY_VERSION = 7
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

    music = " ".join(plan.music.casefold().split())
    if music != "no music. sfx only.":
        _add(
            issues,
            "non_diegetic_music_forbidden",
            "H3 generation permits SFX only and no non-diegetic music",
            "music",
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
    "normalize_h3_action_timeline",
]
