from __future__ import annotations

from pydantic import BaseModel, Field

from .models import CharacterVisualBible


class VisualPromptSnapshot(BaseModel):
    prompt: str
    visual_bible_revision: str
    reference_paths: list[str] = Field(default_factory=list)
    source_kinds: list[str] = Field(default_factory=list)


def _clean_items(values: list[str]) -> list[str]:
    return [value.strip() for value in values if str(value or "").strip()]


def compile_visual_prompt_snapshot(
    *,
    bible: CharacterVisualBible,
    project_style: str,
    reference_paths: list[str],
    portrait_only: bool = False,
) -> VisualPromptSnapshot:
    if bible.status != "confirmed":
        raise ValueError("visual bible must be confirmed before prompt compilation")

    sections: list[str] = []
    style = str(project_style or "").strip()
    if style:
        sections.append(f"Project visual style: {style}")
    if bible.face_shape:
        sections.append(f"Face shape: {bible.face_shape.strip()}")
    facial_features = _clean_items(bible.facial_features)
    if facial_features:
        sections.append(f"Facial features: {', '.join(facial_features)}")
    if bible.hair_style:
        sections.append(f"Hair: {bible.hair_style.strip()}")
    if bible.body_type and not portrait_only:
        sections.append(f"Body type: {bible.body_type.strip()}")
    distinctive = _clean_items(bible.distinctive_features)
    if distinctive:
        sections.append(f"Distinctive identity features: {', '.join(distinctive)}")
    anchors = _clean_items(bible.identity_anchors)
    if anchors:
        sections.append(f"Identity anchors to preserve: {', '.join(anchors)}")
    if not portrait_only:
        for state, description in bible.outfit_states.items():
            clean_state = str(state or "").strip()
            clean_description = str(description or "").strip()
            if clean_state and clean_description:
                sections.append(f"Outfit state [{clean_state}]: {clean_description}")

    references = list(
        dict.fromkeys(
            path.strip() for path in reference_paths if str(path or "").strip()
        )
    )
    source_kinds = ["confirmed_visual_bible"]
    if style:
        source_kinds.append("project_style")
    if references:
        source_kinds.append("explicit_reference")
    return VisualPromptSnapshot(
        prompt=". ".join(sections),
        visual_bible_revision=bible.revision_id,
        reference_paths=references,
        source_kinds=source_kinds,
    )
