"""Deterministic MiniMax H3 wire-prompt compiler for typed director plans."""

from __future__ import annotations

from .h3_director_plan import (
    H3ActionPlan,
    H3CameraPlan,
    H3DialogueCue,
    H3DirectorPlan,
    H3FrameDifference,
    H3ShotPlan,
)
from .models import H3Mode


H3_PROMPT_COMPILER_VERSION = 1


def compile_h3_director_plan(plan: H3DirectorPlan) -> str:
    """Compile a validated I2VA/FL2VA plan into the official wire layout."""
    if plan.mode not in {H3Mode.I2VA, H3Mode.FL2VA}:
        raise ValueError("H3 director prompt compiler supports only i2va and fl2va")

    description = _compile_description(plan)
    sections = (
        ("integrated_multimodal_description", description),
        ("overall_soundscape", plan.soundscape),
        ("non_diegetic_music", plan.music),
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
    return (
        f"{camera.speed}, {camera.amplitude} {camera.type} "
        f"moving {camera.direction}"
    )


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
