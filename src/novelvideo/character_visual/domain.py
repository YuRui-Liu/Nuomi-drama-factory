"""Character-visual domain objects, deliberately independent from generation runners."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class FactKind(str, Enum):
    """Whether a fact is stated by the screenplay or inferred by a reader/model."""

    EXPLICIT = "explicit"
    INFERRED = "inferred"


@dataclass(frozen=True, slots=True)
class SourceSpan:
    source: str
    start: int
    end: int

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("source must not be empty")
        if self.start < 0 or self.end < self.start:
            raise ValueError("source span must satisfy 0 <= start <= end")


@dataclass(frozen=True, slots=True)
class NarrativeFact:
    value: str
    source_span: SourceSpan
    evidence: str
    confidence: float
    kind: FactKind
    visible: bool = True

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("fact value must not be empty")
        if not self.evidence.strip():
            raise ValueError("fact evidence must not be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

    @property
    def eligible_visual_constraint(self) -> bool:
        return self.kind is FactKind.EXPLICIT and self.visible


@dataclass(frozen=True, slots=True)
class CharacterNarrativeProfile:
    """Screenplay-derived facts; legacy prompt material is quarantined, not trusted."""

    character_id: str
    display_name: str
    facts: tuple[NarrativeFact, ...] = ()
    face_prompt: str | None = None
    legacy_untrusted: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.character_id.strip() or not self.display_name.strip():
            raise ValueError("character id and display name must not be empty")


@dataclass(frozen=True, slots=True)
class CharacterDesignBrief:
    character_id: str
    display_name: str
    visual_constraints: tuple[NarrativeFact, ...] = ()
    facial_features: tuple[str, ...] | None = None
    hairstyle: tuple[str, ...] | None = None

    @classmethod
    def from_narrative(
        cls,
        profile: CharacterNarrativeProfile,
        *,
        facial_fact_indexes: Iterable[int] = (),
        hairstyle_fact_indexes: Iterable[int] = (),
    ) -> "CharacterDesignBrief":
        eligible = tuple(fact for fact in profile.facts if fact.eligible_visual_constraint)
        facial = _selected_values(profile.facts, facial_fact_indexes)
        hair = _selected_values(profile.facts, hairstyle_fact_indexes)
        return cls(
            character_id=profile.character_id,
            display_name=profile.display_name,
            visual_constraints=eligible,
            facial_features=facial or None,
            hairstyle=hair or None,
        )


def _selected_values(
    facts: tuple[NarrativeFact, ...], indexes: Iterable[int]
) -> tuple[str, ...]:
    selected: list[str] = []
    for index in indexes:
        fact = facts[index]
        if not fact.eligible_visual_constraint:
            raise ValueError("only explicit visible facts may populate a design field")
        selected.append(fact.value)
    return tuple(selected)


@dataclass(frozen=True, slots=True)
class CharacterVisualBible:
    brief: CharacterDesignBrief
    confirmed: bool = False
    approved_notes: tuple[str, ...] = ()

    @classmethod
    def confirm(
        cls,
        brief: CharacterDesignBrief,
        *,
        approved_notes: Iterable[str] = (),
    ) -> "CharacterVisualBible":
        return cls(
            brief=brief,
            confirmed=True,
            approved_notes=tuple(note.strip() for note in approved_notes if note.strip()),
        )


@dataclass(frozen=True, slots=True)
class PromptInputs:
    project_style: str
    reference_images: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CharacterPromptSnapshot:
    character_id: str
    prompt: str
    inputs: PromptInputs


def compile_character_prompt(
    bible: CharacterVisualBible,
    *,
    project_style: str,
    explicit_reference_images: Iterable[str] = (),
) -> CharacterPromptSnapshot:
    """Compile an immutable downstream snapshot without mutating narrative state."""

    if not bible.confirmed:
        raise ValueError("CharacterVisualBible must be confirmed before compilation")
    if not project_style.strip():
        raise ValueError("project style must not be empty")

    references = tuple(ref.strip() for ref in explicit_reference_images if ref.strip())
    brief = bible.brief
    parts = [f"角色：{brief.display_name}", f"项目风格：{project_style.strip()}"]
    constraints = [fact.value for fact in brief.visual_constraints]
    if constraints:
        parts.append("已确认视觉约束：" + "；".join(constraints))
    if brief.facial_features:
        parts.append("五官：" + "；".join(brief.facial_features))
    if brief.hairstyle:
        parts.append("发型：" + "；".join(brief.hairstyle))
    if bible.approved_notes:
        parts.append("已批准设计补充：" + "；".join(bible.approved_notes))
    if references:
        parts.append("显式参考图：" + "；".join(references))

    inputs = PromptInputs(project_style=project_style.strip(), reference_images=references)
    return CharacterPromptSnapshot(
        character_id=brief.character_id,
        prompt="\n".join(parts),
        inputs=inputs,
    )
