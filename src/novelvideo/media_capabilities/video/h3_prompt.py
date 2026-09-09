"""Compatibility entry points projected onto the canonical MiniMax H3 wire."""

from __future__ import annotations

import re
from collections.abc import Sequence

from .h3_wire import (
    H3BaseWire,
    H3ReferenceWire,
    H3RetentionItem,
    H3Wire,
    compile_h3_wire,
    normalize_h3_music,
    normalize_h3_visual_retention,
)
from .models import H3Mode, MotionSpec

LEGACY_H3_DURATION_SECONDS = 5.0


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


def compile_h3(
    spec: MotionSpec,
    mode: H3Mode,
    *,
    duration_seconds: float = LEGACY_H3_DURATION_SECONDS,
    final_shot_number: int | None = None,
    speaker: str = "",
    tone: str = "",
) -> str:
    """Compile a legacy MotionSpec through the canonical official wire."""
    wire = _project_motion_spec_to_wire(
        spec,
        H3Mode(mode),
        duration_seconds=duration_seconds,
        final_shot_number=final_shot_number,
        speaker=speaker,
        tone=tone,
    )
    return compile_h3_wire(wire)


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
    subject_definitions: Sequence[str] = (),
    summary: str = "",
    retention_analysis: str = "",
) -> str:
    """Render optimizer output through the same canonical wire projection."""
    return compile_h3(
        MotionSpec(
            action=integrated_multimodal_description.strip(),
            dialogue=dialogue or None,
            soundscape=overall_soundscape,
            music=non_diegetic_music,
            subject_definitions=tuple(subject_definitions),
            summary=summary or None,
            retention_analysis=retention_analysis or None,
        ),
        mode,
        duration_seconds=duration_seconds,
        speaker=speaker,
        tone=tone,
    )


def _project_motion_spec_to_wire(
    spec: MotionSpec,
    mode: H3Mode,
    *,
    duration_seconds: float,
    final_shot_number: int | None,
    speaker: str,
    tone: str,
) -> H3Wire:
    description = _shot_description(spec.action)
    if mode is H3Mode.REF2VA:
        definitions = _reference_subject_definitions(spec.subject_definitions)
        subject_tags = tuple(
            match.group(0)
            for definition in definitions
            if (match := re.match(r"<Subject \d+>", definition)) is not None
        )
        detailed_description = _inject_subjects(description, subject_tags)
        detailed_description = _append_dialogue(
            detailed_description,
            spec.dialogue,
            speaker=speaker,
            tone=tone,
        )
        retention = normalize_h3_visual_retention(
            spec.retention_analysis
            or (
                "retain the visible identity and appearance from its source picture"
            )
        )
        summary = str(spec.summary or spec.action).strip()
        if not summary.startswith("[reference generation] "):
            summary = f"[reference generation] {summary}"
        return H3ReferenceWire(
            mode=mode,
            duration_seconds=duration_seconds,
            subject_definitions="\n".join(definitions),
            summary=summary,
            retention_analysis=tuple(
                H3RetentionItem(
                    subject=f"{subject} (appears in [Shot 1])",
                    retain=retention,
                )
                for subject in subject_tags
            ),
            detailed_description=detailed_description,
            overall_soundscape=_nonempty(spec.soundscape),
            non_diegetic_music=normalize_h3_music(spec.music),
        )

    description = _append_dialogue(
        description,
        spec.dialogue,
        speaker=speaker,
        tone=tone,
    )
    resolved_final_shot = final_shot_number or _final_shot_number(description)
    return H3BaseWire(
        mode=mode,
        duration_seconds=duration_seconds,
        final_shot_number=resolved_final_shot,
        integrated_multimodal_description=description,
        overall_soundscape=_nonempty(spec.soundscape),
        non_diegetic_music=normalize_h3_music(spec.music),
    )


def _reference_subject_definitions(values: Sequence[str]) -> tuple[str, ...]:
    descriptions = tuple(str(value).strip() for value in values if str(value).strip())
    if not descriptions:
        descriptions = ("legacy reference subject (description not supplied)",)
    return tuple(
        f"<Subject {index}> from <Picture {index}>: "
        f"{_strip_legacy_subject_prefix(description)}"
        for index, description in enumerate(descriptions, start=1)
    )


def _strip_legacy_subject_prefix(value: str) -> str:
    normalized = re.sub(r"^<Subject \d+>\s*", "", value).strip()
    normalized = re.sub(r"^from <Picture \d+>\s*[:—-]?\s*", "", normalized)
    return normalized or "legacy reference subject (description not supplied)"


def _inject_subjects(description: str, subject_tags: Sequence[str]) -> str:
    missing = [subject for subject in subject_tags if subject not in description]
    if not missing:
        return description
    return description.replace("[Shot 1]", f"[Shot 1] {' '.join(missing)}", 1)


def _shot_description(action: str) -> str:
    description = action.strip()
    return description if description.startswith("[Shot 1]") else f"[Shot 1] {description}"


def _append_dialogue(
    description: str,
    dialogue: str | None,
    *,
    speaker: str,
    tone: str,
) -> str:
    if not str(dialogue or "").strip():
        return description
    speaker_name = speaker.strip()
    speaker_id = _speaker_id(description, speaker_name)
    identity = f"{speaker_name} ({speaker_id})" if speaker_name else f"({speaker_id})"
    delivery = f" with {tone.strip()} delivery" if tone.strip() else ""
    language = _dialogue_language(str(dialogue))
    cue = (
        f"{identity} says{delivery}: "
        f"<d>[{language}]{str(dialogue).strip()}</d>"
    )
    return f"{description}\n{cue}"


def _speaker_id(description: str, speaker: str) -> str:
    if speaker:
        existing = re.search(
            rf"(?<!\w){re.escape(speaker)}\s+\(S(\d+)\)", description
        )
        if existing is not None:
            return f"S{existing.group(1)}"
    used = [int(value) for value in re.findall(r"\(S(\d+)\)", description)]
    return f"S{max(used, default=0) + 1}"


def _dialogue_language(dialogue: str) -> str:
    return (
        "Chinese"
        if any("\u3400" <= char <= "\u9fff" for char in dialogue)
        else "English"
    )


def _final_shot_number(description: str) -> int:
    shots = [int(value) for value in re.findall(r"\[Shot (\d+)\]", description)]
    return shots[-1] if shots else 1


def _nonempty(value: str | None) -> str:
    return str(value or "").strip() or "N/A"


__all__ = [
    "LEGACY_H3_DURATION_SECONDS",
    "compile_h3",
    "render_h3_optimized_prompt",
    "select_mode",
]
