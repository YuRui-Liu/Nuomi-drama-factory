from collections.abc import Sequence

from .models import H3Mode, MotionSpec


def select_mode(
    first_frame: object | None,
    last_frame: object | None,
    references: Sequence[object] | None,
) -> H3Mode:
    has_first = first_frame is not None
    has_last = last_frame is not None
    has_references = bool(references)

    if has_references:
        if has_first or has_last:
            raise ValueError("reference images cannot be combined with frames")
        return H3Mode.REF2VA
    if has_first and has_last:
        return H3Mode.FL2VA
    if has_first:
        return H3Mode.I2VA
    if has_last:
        return H3Mode.L2VA
    return H3Mode.T2VA


def compile_h3(spec: MotionSpec, mode: H3Mode) -> str:
    mode = H3Mode(mode)
    if mode is H3Mode.REF2VA:
        sections = (
            ("subject_definitions", "\n".join(spec.subject_definitions)),
            ("summary", spec.summary or ""),
            ("retention_analysis", spec.retention_analysis or ""),
            ("detailed_description", _description(spec)),
            ("overall_soundscape", spec.soundscape or ""),
            ("non_diegetic_music", spec.music or ""),
        )
    else:
        sections = (
            ("integrated_multimodal_description", _description(spec)),
            ("overall_soundscape", spec.soundscape or ""),
            ("non_diegetic_music", spec.music or ""),
        )

    rendered_sections = [f"mode: {mode.value}"]
    rendered_sections.extend(f"{name}:\n{value}" for name, value in sections)
    return "\n\n".join(rendered_sections)


def render_h3_optimized_prompt(
    *,
    mode: H3Mode,
    integrated_multimodal_description: str,
    overall_soundscape: str,
    non_diegetic_music: str,
    duration_seconds: float,
    dialogue: str = "",
    speaker: str = "",
    tone: str = "",
) -> str:
    """Render typed optimizer output into MiniMax H3's official wire format."""
    mode = H3Mode(mode)
    if mode not in {H3Mode.I2VA, H3Mode.FL2VA}:
        raise ValueError("optimized H3 prompts support only i2va and fl2va")
    description = integrated_multimodal_description.strip()
    if dialogue.strip():
        delivery = f" with {tone.strip()} delivery" if tone.strip() else ""
        language = _dialogue_language(dialogue)
        cue = (
            f"{speaker.strip()} (S1) says{delivery}: "
            f"<d>[{language}]{dialogue.strip()}</d>"
        )
        description = f"{description}\n{cue}"
    sections = (
        ("integrated_multimodal_description", description),
        ("overall_soundscape", overall_soundscape.strip()),
        ("non_diegetic_music", non_diegetic_music.strip()),
    )
    body = "\n\n".join(f"{name}: {value}" for name, value in sections)
    return f"{_frame_alignment(mode, duration_seconds)}\n\n{body}"


def _frame_alignment(mode: H3Mode, duration_seconds: float) -> str:
    if mode is H3Mode.I2VA:
        return (
            "For the target video, at 0.00 seconds into the target video, "
            "<Picture 1> (from [Shot 1]) is fully referenced."
        )
    return (
        "How the reference pictures align with the target video — Picture 1 "
        "(from Shot 1) aligns with the 0.00-second mark of the target video; "
        f"Picture 2 (from Shot 1) aligns with the {duration_seconds:.2f}-second "
        "mark of the target video."
    )


def _dialogue_language(dialogue: str) -> str:
    return "Chinese" if any("\u3400" <= char <= "\u9fff" for char in dialogue) else "English"


def _description(spec: MotionSpec) -> str:
    if spec.dialogue is None:
        return spec.action
    return f"{spec.action}\ndialogue: {spec.dialogue}"
