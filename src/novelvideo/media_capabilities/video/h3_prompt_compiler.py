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
    lines = [
        f"Visual style: {plan.visual_style}.",
        f"Continuity locks: {'; '.join(plan.continuity_locks)}.",
    ]
    if plan.mode is H3Mode.FL2VA:
        lines.append("Frame differences (Picture 1 to Picture 2):")
        lines.extend(_compile_difference(item, plan.fps) for item in plan.frame_differences)
    for shot in plan.shots:
        lines.extend(_compile_shot(shot, plan.fps))
    return "\n".join(lines)


def _compile_difference(difference: H3FrameDifference, fps: int) -> str:
    return (
        f"Converge by frame {difference.convergence_frame} "
        f"({_seconds(difference.convergence_frame, fps)}): {difference.description}"
    )


def _compile_shot(shot: H3ShotPlan, fps: int) -> list[str]:
    shot_label = _shot_label(shot.shot_id)
    lines = [
        f"[{shot_label}] Frames {shot.start_frame}-{shot.end_frame} "
        f"({_range_seconds(shot.start_frame, shot.end_frame, fps)}). "
        f"{shot.framing}; {shot.angle}; focus on {shot.focus}; "
        f"composition: {shot.composition}. Camera: {_camera_text(shot.camera)}."
    ]
    lines.extend(_compile_action(action, fps) for action in shot.actions)
    lines.extend(_compile_dialogue(cue, fps) for cue in shot.dialogue)
    return lines


def _shot_label(shot_id: str) -> str:
    normalized = shot_id.strip()
    if normalized.casefold().startswith("shot "):
        return f"Shot {normalized[5:].strip()}"
    return f"Shot {normalized}"


def _camera_text(camera: H3CameraPlan) -> str:
    if camera.type.casefold() in {"fixed", "locked", "none", "static"}:
        return f"{camera.type} camera"
    return (
        f"{camera.speed}, {camera.amplitude} {camera.type} "
        f"moving {camera.direction}"
    )


def _compile_action(action: H3ActionPlan, fps: int) -> str:
    return (
        f"Action {action.phase}, frames {action.start_frame}-{action.end_frame} "
        f"({_range_seconds(action.start_frame, action.end_frame, fps)}): "
        f"{action.description}"
    )


def _compile_dialogue(cue: H3DialogueCue, fps: int) -> str:
    markers = "".join(
        f" [{marker}]"
        for marker, enabled in (
            ("continuation", cue.continuation),
            ("truncated", cue.truncated),
        )
        if enabled
    )
    return (
        f"{cue.speaker} ({cue.speaker_id}) says at frames "
        f"{cue.start_frame}-{cue.end_frame} "
        f"({_range_seconds(cue.start_frame, cue.end_frame, fps)}): "
        f"<d>[{cue.language}]{cue.text}</d>{markers}"
    )


def _range_seconds(start_frame: int, end_frame: int, fps: int) -> str:
    return f"{start_frame / fps:.2f}-{_seconds(end_frame, fps)}"


def _seconds(frame: int, fps: int) -> str:
    return f"{frame / fps:.2f}s"


compile_h3_prompt = compile_h3_director_plan


__all__ = [
    "H3_PROMPT_COMPILER_VERSION",
    "compile_h3_director_plan",
    "compile_h3_prompt",
]
