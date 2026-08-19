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
    """Render typed optimizer output into the fixed H3 wire format."""
    mode = H3Mode(mode)
    if mode not in {H3Mode.I2VA, H3Mode.FL2VA}:
        raise ValueError("optimized H3 prompts support only i2va and fl2va")
    description = integrated_multimodal_description.strip()
    if dialogue.strip():
        cue = f"{speaker.strip()}（{tone.strip()}）说：“{dialogue.strip()}”"
        description = f"{description}\n[00:00.000-{_timestamp(duration_seconds)}] {cue}"
    sections = (
        ("frame_alignment", _frame_alignment(mode, duration_seconds)),
        ("integrated_multimodal_description", description),
        ("overall_soundscape", overall_soundscape.strip()),
        ("non_diegetic_music", non_diegetic_music.strip()),
    )
    body = "\n\n".join(f"{name}:\n{value}" for name, value in sections)
    return f"mode: {mode.value}\n\n{body}"


def _frame_alignment(mode: H3Mode, duration_seconds: float) -> str:
    if mode is H3Mode.I2VA:
        return (
            "图片1：0.00 秒。首帧为动作起点；后续运动须连续，"
            "并保持首帧中的人物、场景与空间关系。"
        )
    return (
        f"图片1：0.00 秒；图片2：{duration_seconds:.2f} 秒。"
        "首帧为动作起点，尾帧为动作终点；"
        "所有运动连续且不可偏离两帧可见事实。"
    )


def _timestamp(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    minutes, remainder = divmod(total_ms, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"


def _description(spec: MotionSpec) -> str:
    if spec.dialogue is None:
        return spec.action
    return f"{spec.action}\ndialogue: {spec.dialogue}"
