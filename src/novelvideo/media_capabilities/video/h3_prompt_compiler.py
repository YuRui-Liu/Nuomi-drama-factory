"""Deterministic projection from typed H3 director plans to official wire prompts."""

from __future__ import annotations

import re
from typing import TypeVar

from .h3_director_plan import (
    H3ActionPlan,
    H3CameraPlan,
    H3DialogueCue,
    H3DirectorPlan,
    H3FrameDifference,
    H3ReferenceSubjectPlan,
    H3ShotPlan,
)
from .h3_rigid_prompt import (
    H3ActiveReference,
    H3CharacterActingPlan,
    H3LightingPlan,
    H3LocationMapPlan,
    H3OpticsPlan,
    H3PositiveConstraint,
    H3RigidPromptPlan,
    H3SpatialBlockingPlan,
)
from .h3_wire import (
    H3BaseWire,
    H3ReferenceWire,
    H3RetentionItem,
    H3Wire,
    compile_h3_wire,
)
from .models import H3Mode


H3_PROMPT_COMPILER_VERSION = 3
_SHOT_SCOPED = TypeVar("_SHOT_SCOPED", H3SpatialBlockingPlan, H3OpticsPlan)
_NO_MUSIC = frozenset(
    {"", "n/a", "none", "none.", "no music", "no music.", "no music. sfx only."}
)
_ASSERTION_COUNT_RE = re.compile(
    r"\b(?:exactly\s+)?(?:(?:a\s+)?single|(?:a\s+)?pair\s+of|no|zero|one|"
    r"two|three|four|five|six|seven|eight|nine|ten|[0-9]+)\b\s*",
    flags=re.IGNORECASE,
)


def compile_h3_director_plan(plan: H3DirectorPlan) -> str:
    """Compile a validated director plan through the official H3 wire compiler."""
    return compile_h3_wire(project_director_plan_to_wire(plan))


def project_director_plan_to_wire(plan: H3DirectorPlan) -> H3Wire:
    """Project an internal director plan onto its mode-specific official wire."""
    description = _compile_playback_description(plan)
    common = {
        "mode": plan.mode,
        "duration_seconds": plan.total_frames / plan.fps,
        "overall_soundscape": plan.soundscape,
        "non_diegetic_music": _normalize_music(plan.music),
    }
    if plan.mode is H3Mode.REF2VA:
        return H3ReferenceWire(
            **common,
            subject_definitions=_compile_subject_definitions(plan.reference_subjects),
            summary=f"[reference generation] {plan.reference_summary}",
            retention_analysis=tuple(
                _compile_retention_item(subject)
                for subject in plan.reference_subjects
            ),
            detailed_description=description,
        )
    return H3BaseWire(
        **common,
        final_shot_number=len(plan.shots),
        integrated_multimodal_description=description,
    )


def _compile_playback_description(plan: H3DirectorPlan) -> str:
    rigid = plan.rigid_prompt
    blocking_by_shot: dict[str, H3SpatialBlockingPlan] = {}
    optics_by_shot: dict[str, H3OpticsPlan] = {}
    if rigid is not None:
        blocking_by_shot = {
            item.shot_id: item
            for item in _ordered_shot_scoped(
                plan, rigid.spatial_blocking, "spatial_blocking"
            )
        }
        optics_by_shot = {
            item.shot_id: item
            for item in _ordered_shot_scoped(plan, rigid.optics, "optics")
        }

    lines: list[str] = []
    subjects_by_shot = _reference_subjects_by_shot(plan)
    speaker_subjects = {
        subject.speaker_id: subject.subject_index
        for subject in plan.reference_subjects
        if subject.speaker_id is not None
    }
    for shot_index, shot in enumerate(plan.shots):
        lines.append(
            _compile_shot_intro(
                plan,
                shot,
                first=shot_index == 0,
                rigid=rigid,
                blocking=blocking_by_shot.get(shot.shot_id),
                optics=optics_by_shot.get(shot.shot_id),
                reference_subjects=subjects_by_shot.get(shot.shot_id, ()),
            )
        )
        lines.extend(
            _compile_shot_events(
                plan,
                shot,
                shot_index=shot_index,
                speaker_subjects=speaker_subjects,
            )
        )
    return "\n".join(lines)


def _compile_shot_intro(
    plan: H3DirectorPlan,
    shot: H3ShotPlan,
    *,
    first: bool,
    rigid: H3RigidPromptPlan | None,
    blocking: H3SpatialBlockingPlan | None,
    optics: H3OpticsPlan | None,
    reference_subjects: tuple[H3ReferenceSubjectPlan, ...],
) -> str:
    label = _shot_label(shot.shot_id)
    if first:
        opening = (
            f"[{label}] Render in {_with_indefinite_article(plan.visual_style)} "
            f"visual style. Frame {shot.focus} in "
            f"{_with_indefinite_article(shot.framing)} from {shot.angle}; "
            f"{_sentence(shot.composition)}"
        )
    else:
        opening = (
            f"[{label}] At {_timestamp(shot.start_frame, plan.fps)}, "
            f"cut to {_with_indefinite_article(shot.framing)} from {shot.angle}, "
            f"focused on {shot.focus}; "
            f"{_sentence(shot.composition)}"
        )
    parts = [f"{opening} The {_camera_text(shot.camera)}."]
    if first:
        parts.append(f" Throughout, preserve {'; '.join(plan.continuity_locks)}.")
        if rigid is not None:
            parts.append(" " + _compile_rigid_global_facts(rigid, plan.visual_style))
    if reference_subjects:
        rendered = "; ".join(
            f"<Subject {subject.subject_index}> is {subject.description}"
            for subject in reference_subjects
        )
        parts.append(f" In this shot, {rendered}.")
    if blocking is not None:
        parts.append(" " + _compile_spatial_fact(blocking))
    if optics is not None:
        parts.append(" " + _compile_optics_fact(optics))
    return "".join(parts)


def _compile_rigid_global_facts(
    rigid: H3RigidPromptPlan, visual_style: str
) -> str:
    context = rigid.scene_context
    parts = [
        context.summary,
        _compile_character_count_fact(
            context.exact_character_count, context.active_characters
        ),
        _compile_active_references(rigid.active_references),
        _compile_location_fact(rigid.location_map),
        _compile_format_fact(rigid),
        _compile_lighting_fact(rigid.lighting),
        _compile_acting_fact(rigid.character_acting),
    ]
    if rigid.style_prefix != visual_style:
        parts.append(f"Rendering follows {rigid.style_prefix}.")
    parts.extend(
        f"Movement remains physically grounded: {_strip_terminal(statement)}."
        for statement in rigid.physics.statements
    )
    parts.extend(
        f"Image quality must preserve {_lower_initial(requirement)}."
        for requirement in rigid.quality.requirements
    )
    parts.extend(_compile_positive_fact(item) for item in rigid.positive_constraints)
    return " ".join(_sentence(part) for part in parts if part)


def _compile_character_count_fact(
    count: int, active_characters: tuple[str, ...]
) -> str:
    if count == 0:
        return "No visible characters are present."
    names = _natural_list(active_characters) if active_characters else ""
    if count == 1:
        subject = f", {names}," if names else ""
        return f"Exactly one visible character{subject} is present without duplicates."
    subject = f", {names}," if names else ""
    return (
        f"Exactly {count} visible characters{subject} are present without duplicates."
    )


def _compile_active_references(references: tuple[H3ActiveReference, ...]) -> str:
    facts = []
    for reference in references:
        inherited = ", ".join(reference.inherit) or "no attributes"
        excluded = ", ".join(reference.exclude) or "nothing"
        facts.append(
            f"Resolved {reference.kind} reference {reference.tag} supplies "
            f"{reference.role}, inheriting only {inherited} and excluding {excluded}"
        )
    return "; ".join(facts)


def _compile_location_fact(location: H3LocationMapPlan) -> str:
    geography = _lower_initial(_strip_terminal(location.geography))
    landmarks = _natural_list(location.landmarks)
    return (
        f"The scene occupies {geography}. Its fixed landmarks are {landmarks}. "
        f"The camera remains {location.camera_side}, respecting {location.axis}."
    )


def _compile_format_fact(rigid: H3RigidPromptPlan) -> str:
    format_plan = rigid.format_mode
    take = format_plan.mode.replace("_", " ")
    timing = "in real time" if format_plan.real_time else "with altered time"
    parts = [
        f"The sequence runs as a {take} for "
        f"{format_plan.total_duration_seconds:.2f} seconds {timing}"
    ]
    if format_plan.speed_ramps:
        parts.append(f"using {'; '.join(format_plan.speed_ramps)}")
    if format_plan.cut_points_seconds:
        cuts = ", ".join(
            f"{point:.2f} seconds" for point in format_plan.cut_points_seconds
        )
        parts.append(f"with cuts at {cuts}")
    return ", ".join(parts)


def _compile_lighting_fact(lighting: H3LightingPlan) -> str:
    return (
        f"The lighting follows one coherent source: "
        f"{_strip_terminal(lighting.source_logic)}. The primary light source is "
        f"{lighting.primary_source} originating {lighting.origin}; it casts light "
        f"{lighting.direction} and shadows {lighting.shadow_direction}. Its quality "
        f"is {lighting.quality}, with {lighting.color}. On the subjects, "
        f"{_lower_initial(_strip_terminal(lighting.subject_effect))}. In the "
        f"environment, {_lower_initial(_strip_terminal(lighting.environment_effect))}. "
        f"Use {_lower_initial(_strip_terminal(lighting.fill_logic))}. Preserve "
        f"{_lower_initial(_strip_terminal(lighting.catchlight))}. "
        f"{_capitalize_initial(_sentence(lighting.contact_shadows))} Maintain the same "
        f"{lighting.continuity_key} lighting logic throughout."
    )


def _compile_acting_fact(
    acting_plans: tuple[H3CharacterActingPlan, ...],
) -> str:
    return "; ".join(
        f"{acting.character_id} appears {acting.state}, moving with "
        f"{acting.body_rhythm}; {acting.visible_behavior}; {acting.change}"
        for acting in acting_plans
    )


def _compile_positive_fact(constraint: H3PositiveConstraint) -> str:
    if constraint.count is None:
        return constraint.assertion
    semantic_assertion = _ASSERTION_COUNT_RE.sub(
        "", _strip_terminal(constraint.assertion)
    ).strip()
    semantic_assertion = re.sub(r"\s{2,}", " ", semantic_assertion)
    semantic = (
        _sentence(_capitalize_initial(semantic_assertion))
        if semantic_assertion
        else ""
    )
    count = {0: "zero", 1: "one"}.get(constraint.count, str(constraint.count))
    target_forms = {
        "characters": ("character", "characters"),
        "references": ("reference", "references"),
        "props": ("prop", "props"),
        "other": ("item", "items"),
    }
    target = target_forms[constraint.target][constraint.count != 1]
    count_fact = f"Keep exactly {count} {target} visible."
    return f"{semantic} {count_fact}".strip()


def _compile_spatial_fact(blocking: H3SpatialBlockingPlan) -> str:
    subjects = "; ".join(
        f"{subject.character_id} stays {subject.position}, facing {subject.facing}, "
        f"looking {subject.gaze}"
        + (f", holding {', '.join(subject.held_props)}" if subject.held_props else "")
        for subject in blocking.subjects
    )
    return _sentence(f"{blocking.summary} {subjects}")


def _compile_optics_fact(optics: H3OpticsPlan) -> str:
    return _sentence(
        f"Use {optics.lens_or_fov} at {optics.camera_height}, "
        f"{optics.subject_distance} from the subject, with "
        f"{optics.depth_of_field}; {optics.focus_plan}"
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


def _compile_shot_events(
    plan: H3DirectorPlan,
    shot: H3ShotPlan,
    *,
    shot_index: int,
    speaker_subjects: dict[str, int],
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
                subject_index=speaker_subjects.get(cue.speaker_id),
            ),
        )
        for cue_index, cue in enumerate(shot.dialogue)
    )
    if plan.mode in {H3Mode.FL2VA, H3Mode.L2VA}:
        events.extend(
            (
                difference.convergence_frame,
                2,
                _compile_difference(
                    difference,
                    plan.fps,
                    target_picture=(
                        "Picture 2"
                        if plan.mode is H3Mode.FL2VA
                        else "<Picture 1>"
                    ),
                ),
            )
            for difference in plan.frame_differences
            if shot.start_frame <= difference.convergence_frame < shot.end_frame
        )
    return [rendered for _, _, rendered in sorted(events)]


def _compile_action(action: H3ActionPlan, fps: int) -> str:
    if action.start_frame == 0 and action.phase == "establish":
        return _sentence(action.description)
    return f"At {_timestamp(action.start_frame, fps)}, {action.description}"


def _compile_difference(
    difference: H3FrameDifference, fps: int, *, target_picture: str
) -> str:
    return (
        f"At {_timestamp(difference.convergence_frame, fps)}, the image converges "
        f"toward {target_picture} as {difference.description}"
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
        return "carries over from the previous shot and continues across the cut"
    if outgoing:
        return "continues across the cut"
    if incoming:
        return "carries over from the previous shot"
    raise ValueError("validated continuation cue has no adjacent shot role")


def _compile_dialogue(
    cue: H3DialogueCue,
    fps: int,
    *,
    continuation_role: str | None,
    subject_index: int | None,
) -> str:
    speaker = (
        f"<Subject {subject_index}> ({cue.speaker_id})"
        if subject_index is not None
        else f"{cue.speaker} ({cue.speaker_id})"
    )
    transition = "<scenetrans>" if cue.continuation else ""
    cutoff = "<cutoff>" if cue.truncated else ""
    delivery = continuation_role or "says"
    text = (
        f"At {_timestamp(cue.start_frame, fps)}, {speaker} {delivery}: "
        f"{transition}<d>[{cue.language}]{cue.text}</d>{cutoff}"
    )
    details = tuple(
        detail
        for detail in (
            f"in a {cue.voice_descriptor}" if cue.voice_descriptor else None,
            cue.delivery,
            cue.physical_action,
            cue.facial_reaction,
        )
        if detail
    )
    if details:
        text += ". " + _sentence("; ".join(details))
    return text


def _camera_text(camera: H3CameraPlan) -> str:
    if camera.is_static:
        return "camera remains static"
    modifiers = tuple(
        value
        for value in (camera.speed, camera.amplitude)
        if value is not None and value.casefold() not in {"normal", "medium"}
    )
    prefix = f"{' '.join(modifiers)} " if modifiers else ""
    return f"camera makes a {prefix}{camera.type} moving {camera.direction}"


def _reference_subjects_by_shot(
    plan: H3DirectorPlan,
) -> dict[str, tuple[H3ReferenceSubjectPlan, ...]]:
    by_shot: dict[str, list[H3ReferenceSubjectPlan]] = {}
    for subject in plan.reference_subjects:
        for shot_id in subject.shot_ids:
            by_shot.setdefault(shot_id, []).append(subject)
    return {shot_id: tuple(subjects) for shot_id, subjects in by_shot.items()}


def _compile_subject_definitions(
    subjects: tuple[H3ReferenceSubjectPlan, ...],
) -> str:
    return "\n".join(
        f"<Subject {subject.subject_index}> comes from "
        f"{_picture_list(subject.source_picture_indexes)}: {subject.description}."
        for subject in subjects
    )


def _picture_list(indexes: tuple[int, ...]) -> str:
    pictures = tuple(f"<Picture {index}>" for index in indexes)
    if len(pictures) == 1:
        return pictures[0]
    if len(pictures) == 2:
        return " and ".join(pictures)
    return f"{', '.join(pictures[:-1])}, and {pictures[-1]}"


def _compile_retention_item(subject: H3ReferenceSubjectPlan) -> H3RetentionItem:
    shots = ", ".join(f"[Shot {shot_id}]" for shot_id in subject.shot_ids)
    return H3RetentionItem(
        subject=f"<Subject {subject.subject_index}> (appears in {shots})",
        retain=f"{subject.retention_marker} - {subject.retention_detail}",
    )


def _normalize_music(music: str) -> str:
    normalized = music.strip()
    return "N/A" if normalized.casefold() in _NO_MUSIC else normalized


def _sentence(value: str) -> str:
    normalized = value.strip()
    if not normalized or normalized.endswith((".", "!", "?")):
        return normalized
    return f"{normalized}."


def _strip_terminal(value: str) -> str:
    return value.strip().rstrip(".!?")


def _lower_initial(value: str) -> str:
    if value.startswith(("A ", "An ", "The ")):
        return value[0].lower() + value[1:]
    return value


def _capitalize_initial(value: str) -> str:
    return value[:1].upper() + value[1:]


def _natural_list(values: tuple[str, ...]) -> str:
    if not values:
        return "none"
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return " and ".join(values)
    return f"{', '.join(values[:-1])}, and {values[-1]}"


def _with_indefinite_article(value: str) -> str:
    normalized = value.strip()
    if re.match(r"^(?:a|an|the)\s+", normalized, flags=re.IGNORECASE):
        return normalized
    article = "an" if normalized[:1].casefold() in "aeiou" else "a"
    return f"{article} {normalized}"


def _shot_label(shot_id: str) -> str:
    normalized = shot_id.strip()
    if normalized.casefold().startswith("shot "):
        return f"Shot {normalized[5:].strip()}"
    return f"Shot {normalized}"


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
    "project_director_plan_to_wire",
]
