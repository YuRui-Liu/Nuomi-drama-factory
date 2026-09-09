"""Deterministic MiniMax H3 wire-prompt compiler for typed director plans."""

from __future__ import annotations

from typing import TypeVar

from .h3_director_plan import (
    H3ActionPlan,
    H3CameraPlan,
    H3DialogueCue,
    H3DirectorPlan,
    H3FrameDifference,
    H3ShotPlan,
)
from .h3_rigid_prompt import (
    H3ActiveReference,
    H3CharacterActingPlan,
    H3LightingPlan,
    H3LocationMapPlan,
    H3OpticsPlan,
    H3PositiveConstraint,
    H3_RIGID_SECTION_ORDER,
    H3RigidPromptPlan,
    H3SpatialBlockingPlan,
)
from .models import H3Mode


H3_PROMPT_COMPILER_VERSION = 2
_SHOT_SCOPED = TypeVar("_SHOT_SCOPED", H3SpatialBlockingPlan, H3OpticsPlan)


def compile_h3_director_plan(plan: H3DirectorPlan) -> str:
    """Compile a validated I2VA/FL2VA plan into the official wire layout."""
    if plan.mode not in {H3Mode.I2VA, H3Mode.FL2VA}:
        raise ValueError("H3 director prompt compiler supports only i2va and fl2va")

    is_rigid = plan.schema_version >= 2
    if is_rigid and plan.rigid_prompt is None:
        raise ValueError("schema_version>=2 requires rigid_prompt before compilation")
    description = (
        _compile_rigid_description(plan, plan.rigid_prompt)
        if is_rigid and plan.rigid_prompt is not None
        else _compile_description(plan)
    )
    sections = (
        ("integrated_multimodal_description", description),
        ("overall_soundscape", plan.soundscape),
        (
            "non_diegetic_music",
            "No music. SFX only." if is_rigid else plan.music,
        ),
    )
    body = "\n\n".join(f"{name}: {value}" for name, value in sections)
    return f"{_frame_alignment(plan)}\n\n{body}"


def _frame_alignment(plan: H3DirectorPlan) -> str:
    if plan.mode is H3Mode.I2VA:
        return (
            "For the target video, at 0.00 seconds into the target video, "
            "<Picture 1> (from [Shot 1]) is fully referenced."
        )
    end_seconds = plan.total_frames / plan.fps
    return (
        "How the reference pictures align with the target video — Picture 1 "
        "(from Shot 1) aligns with the 0.00-second mark of the target video; "
        f"Picture 2 (from Shot 1) aligns with the {end_seconds:.2f}-second "
        "mark of the target video."
    )


def _compile_description(plan: H3DirectorPlan) -> str:
    lines: list[str] = []
    for index, shot in enumerate(plan.shots):
        lines.append(_compile_shot_heading(plan, shot, first=index == 0))
        if index == 0 and plan.mode is H3Mode.FL2VA:
            lines.append("Picture 1 to Picture 2 differences:")
        lines.extend(_compile_shot_events(plan, shot, shot_index=index))
    return "\n".join(lines)


def _compile_rigid_description(
    plan: H3DirectorPlan, rigid: H3RigidPromptPlan
) -> str:
    spatial_blocking = _ordered_shot_scoped(
        plan, rigid.spatial_blocking, "spatial_blocking"
    )
    optics = _ordered_shot_scoped(plan, rigid.optics, "optics")
    bodies = (
        _compile_scene_context(rigid),
        _compile_active_references(rigid.active_references),
        _compile_location_map(rigid.location_map),
        _compile_spatial_blocking(spatial_blocking),
        _compile_format(rigid),
        _compile_optics(optics),
        _compile_rigid_camera(plan),
        _compile_rigid_action_timing(plan),
        "\n".join(rigid.physics.statements),
        _compile_lighting(rigid.lighting),
        _compile_rigid_audio(plan),
        _compile_character_acting(rigid.character_acting),
        rigid.style_prefix,
        "\n".join(rigid.quality.requirements),
        _compile_positive_constraints(rigid.positive_constraints),
    )
    return "\n\n".join(
        f"{heading}\n{body}"
        for heading, body in zip(H3_RIGID_SECTION_ORDER, bodies, strict=True)
    )


def _ordered_shot_scoped(
    plan: H3DirectorPlan,
    values: tuple[_SHOT_SCOPED, ...],
    field_name: str,
) -> tuple[_SHOT_SCOPED, ...]:
    expected = tuple(shot.shot_id for shot in plan.shots)
    by_id = {value.shot_id: value for value in values}
    if len(by_id) != len(values) or set(by_id) != set(expected):
        raise ValueError(
            f"{field_name} shot_id values must match director plan shots exactly"
        )
    return tuple(by_id[shot_id] for shot_id in expected)


def _compile_scene_context(rigid: H3RigidPromptPlan) -> str:
    context = rigid.scene_context
    active = ", ".join(context.active_characters) or "none"
    return "\n".join(
        (
            f"EXACT {context.exact_character_count} CHARACTERS — NO DUPLICATES",
            f"Active characters: {active}.",
            context.summary,
        )
    )


def _compile_active_references(references: tuple[H3ActiveReference, ...]) -> str:
    return "\n".join(
        f"{reference.tag} ({reference.kind}) — role: {reference.role}; "
        f"inherit only: {', '.join(reference.inherit) or 'none'}; "
        f"exclude: {', '.join(reference.exclude) or 'none'}."
        for reference in references
    )


def _compile_location_map(location: H3LocationMapPlan) -> str:
    return "\n".join(
        (
            location.geography,
            f"Landmarks: {'; '.join(location.landmarks)}.",
            f"Camera side: {location.camera_side}.",
            f"Action axis: {location.axis}.",
        )
    )


def _compile_spatial_blocking(
    blocking_plans: tuple[H3SpatialBlockingPlan, ...],
) -> str:
    lines: list[str] = []
    for blocking in blocking_plans:
        lines.append(f"[{_shot_label(blocking.shot_id)}] {blocking.summary}")
        lines.extend(
            f"{subject.character_id}: position {subject.position}; facing "
            f"{subject.facing}; gaze {subject.gaze}; held props: "
            f"{', '.join(subject.held_props) or 'none'}."
            for subject in blocking.subjects
        )
    return "\n".join(lines)


def _compile_format(rigid: H3RigidPromptPlan) -> str:
    format_mode = rigid.format_mode
    cut_points = ", ".join(
        f"{cut_point:.2f} seconds" for cut_point in format_mode.cut_points_seconds
    )
    return (
        f"Mode: {format_mode.mode}; duration: "
        f"{format_mode.total_duration_seconds:.2f} seconds; real time: "
        f"{'yes' if format_mode.real_time else 'no'}; speed ramps: "
        f"{'; '.join(format_mode.speed_ramps) or 'none'}; cut points: "
        f"{cut_points or 'none'}."
    )


def _compile_optics(optics_plans: tuple[H3OpticsPlan, ...]) -> str:
    return "\n".join(
        f"[{_shot_label(optics.shot_id)}] {optics.lens_or_fov}; camera height: "
        f"{optics.camera_height}; subject distance: {optics.subject_distance}; "
        f"depth of field: {optics.depth_of_field}; focus: {optics.focus_plan}."
        for optics in optics_plans
    )


def _compile_rigid_camera(plan: H3DirectorPlan) -> str:
    return "\n".join(
        f"[{_shot_label(shot.shot_id)}] {shot.framing}; {shot.angle}; focus on "
        f"{shot.focus}; composition: {shot.composition}. Camera: "
        f"{_camera_text(shot.camera)}."
        for shot in plan.shots
    )


def _compile_rigid_action_timing(plan: H3DirectorPlan) -> str:
    lines = [
        f"[{_shot_label(shot.shot_id)}] {_compile_action(action, plan.fps)}"
        for shot in plan.shots
        for action in shot.actions
    ]
    if plan.mode is H3Mode.FL2VA:
        lines.extend(
            f"[Shot 1] {_compile_difference(difference, plan.fps)}"
            for difference in plan.frame_differences
        )
    return "\n".join(lines)


def _compile_lighting(lighting: H3LightingPlan) -> str:
    return "\n".join(
        (
            f"Source logic: {lighting.source_logic}",
            f"Primary source: {lighting.primary_source}; origin: {lighting.origin}.",
            f"Direction: {lighting.direction}; shadows: {lighting.shadow_direction}.",
            f"Quality: {lighting.quality}; color: {lighting.color}.",
            f"Subject effect: {lighting.subject_effect}",
            f"Environment effect: {lighting.environment_effect}",
            f"Fill logic: {lighting.fill_logic}",
            f"Catchlight: {lighting.catchlight}",
            f"Contact shadows: {lighting.contact_shadows}",
            f"Continuity key: {lighting.continuity_key}",
        )
    )


def _compile_rigid_audio(plan: H3DirectorPlan) -> str:
    lines = [f"Soundscape and SFX: {plan.soundscape}"]
    for shot_index, shot in enumerate(plan.shots):
        for cue_index, cue in enumerate(shot.dialogue):
            role = _continuation_role(
                plan, shot, shot_index=shot_index, cue_index=cue_index
            )
            details = tuple(
                detail
                for detail in (
                    f"voice: {cue.voice_descriptor}" if cue.voice_descriptor else None,
                    f"delivery: {cue.delivery}" if cue.delivery else None,
                    f"physical action: {cue.physical_action}"
                    if cue.physical_action
                    else None,
                    f"facial reaction: {cue.facial_reaction}"
                    if cue.facial_reaction
                    else None,
                )
                if detail is not None
            )
            suffix = f" ({'; '.join(details)})" if details else ""
            lines.append(
                f"[{_shot_label(shot.shot_id)}] "
                f"{_compile_dialogue(cue, plan.fps, continuation_role=role)}{suffix}"
            )
    return "\n".join(lines)


def _compile_character_acting(
    acting_plans: tuple[H3CharacterActingPlan, ...],
) -> str:
    return "\n".join(
        f"{acting.character_id}: state {acting.state}; wants {acting.want}; "
        f"hides {acting.hidden}; body rhythm: {acting.body_rhythm}; visible behavior: "
        f"{acting.visible_behavior}; change: {acting.change}."
        for acting in acting_plans
    )


def _compile_positive_constraints(
    constraints: tuple[H3PositiveConstraint, ...],
) -> str:
    return "\n".join(
        (
            f"{constraint.assertion} (exact count: {constraint.count})."
            if constraint.count is not None
            else f"{constraint.assertion}."
        )
        for constraint in constraints
    )


def _compile_difference(difference: H3FrameDifference, fps: int) -> str:
    return (
        f"At {_timestamp(difference.convergence_frame, fps)}, "
        f"converge toward Picture 2: {difference.description}"
    )


def _compile_shot_heading(
    plan: H3DirectorPlan, shot: H3ShotPlan, *, first: bool
) -> str:
    shot_label = _shot_label(shot.shot_id)
    setup = (
        f"{shot.framing}; {shot.angle}; focus on {shot.focus}; "
        f"composition: {shot.composition}. Camera: {_camera_text(shot.camera)}."
    )
    if first:
        locks = "; ".join(plan.continuity_locks)
        return (
            f"[{shot_label}] {plan.visual_style} visual style; "
            f"continuity locks: {locks}. {setup}"
        )
    return (
        f"[{shot_label}] At {_timestamp(shot.start_frame, plan.fps)}, "
        f"the camera cuts to: {setup}"
    )


def _compile_shot_events(
    plan: H3DirectorPlan, shot: H3ShotPlan, *, shot_index: int
) -> list[str]:
    events: list[tuple[int, int, str]] = []
    events.extend(
        (action.start_frame, 0, _compile_action(action, plan.fps))
        for action in shot.actions
    )
    events.extend(
        (
            cue.start_frame,
            1,
            _compile_dialogue(
                cue,
                plan.fps,
                continuation_role=_continuation_role(
                    plan, shot, shot_index=shot_index, cue_index=cue_index
                ),
            ),
        )
        for cue_index, cue in enumerate(shot.dialogue)
    )
    if plan.mode is H3Mode.FL2VA:
        events.extend(
            (
                difference.convergence_frame,
                2,
                _compile_difference(difference, plan.fps),
            )
            for difference in plan.frame_differences
            if shot.start_frame <= difference.convergence_frame < shot.end_frame
        )
    return [rendered for _, _, rendered in sorted(events)]


def _shot_label(shot_id: str) -> str:
    normalized = shot_id.strip()
    if normalized.casefold().startswith("shot "):
        return f"Shot {normalized[5:].strip()}"
    return f"Shot {normalized}"


def _camera_text(camera: H3CameraPlan) -> str:
    if camera.is_static:
        return f"{camera.type} camera"
    if camera.speed is not None and camera.amplitude is not None:
        prefix = f"{camera.speed}, {camera.amplitude} "
    elif camera.speed is not None:
        prefix = f"{camera.speed} "
    elif camera.amplitude is not None:
        prefix = f"{camera.amplitude} "
    else:
        prefix = ""
    return f"{prefix}{camera.type} moving {camera.direction}"


def _compile_action(action: H3ActionPlan, fps: int) -> str:
    return (
        f"At {_timestamp(action.start_frame, fps)}, {action.phase}: "
        f"{action.description}"
    )


def _continuation_role(
    plan: H3DirectorPlan,
    shot: H3ShotPlan,
    *,
    shot_index: int,
    cue_index: int,
) -> str | None:
    cue = shot.dialogue[cue_index]
    if not cue.continuation:
        return None
    incoming = shot_index > 0 and cue_index == 0
    outgoing = shot_index < len(plan.shots) - 1 and cue_index == len(shot.dialogue) - 1
    if incoming and outgoing:
        return "carries over from the previous shot and continues seamlessly across the cut"
    if outgoing:
        return "continues seamlessly across the cut"
    if incoming:
        return "carries over from the previous shot"
    raise ValueError("validated continuation cue has no adjacent shot role")


def _compile_dialogue(
    cue: H3DialogueCue, fps: int, *, continuation_role: str | None
) -> str:
    prefix = "<scenetrans>" if cue.continuation else ""
    suffix = "<cutoff>" if cue.truncated else ""
    delivery = continuation_role or "says"
    return (
        f"At {_timestamp(cue.start_frame, fps)}, {cue.speaker} "
        f"({cue.speaker_id}) {delivery}: "
        f"{prefix}<d>[{cue.language}]{cue.text}</d>{suffix}"
    )


def _timestamp(frame: int, fps: int) -> str:
    total_milliseconds = round(frame * 1000 / fps)
    minutes, remainder = divmod(total_milliseconds, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


compile_h3_prompt = compile_h3_director_plan


__all__ = [
    "H3_PROMPT_COMPILER_VERSION",
    "compile_h3_director_plan",
    "compile_h3_prompt",
]
