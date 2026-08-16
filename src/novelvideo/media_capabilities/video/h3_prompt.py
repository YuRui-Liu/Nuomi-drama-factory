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


def _description(spec: MotionSpec) -> str:
    if spec.dialogue is None:
        return spec.action
    return f"{spec.action}\ndialogue: {spec.dialogue}"
