from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SourceSpan(BaseModel):
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_order(self) -> "SourceSpan":
        if self.end_line < self.start_line:
            raise ValueError("source span end_line must not precede start_line")
        return self


class CharacterNarrativeFact(BaseModel):
    fact_id: str = Field(min_length=1)
    field: str = Field(min_length=1)
    value: str = Field(min_length=1)
    source_span: SourceSpan
    evidence: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    assertion: Literal["explicit", "inferred"] = "explicit"
    trust: Literal["trusted", "legacy_untrusted"] = "trusted"


_VISIBLE_SCRIPT_FACT_FIELDS = frozenset(
    {
        "age_range",
        "gender",
        "body_type",
        "hair_style",
        "face_shape",
        "facial_feature",
        "distinctive_feature",
        "scar",
        "disability",
        "uniform",
        "clothing_state",
        "injury_state",
    }
)


class CharacterNarrativeProfile(BaseModel):
    character_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    biography: str = ""
    occupation: str = ""
    social_identity: str = ""
    relationships: list[str] = Field(default_factory=list)
    personality: list[str] = Field(default_factory=list)
    dramatic_function: str = ""
    facts: list[CharacterNarrativeFact] = Field(default_factory=list)

    def visual_constraints(self) -> list[CharacterNarrativeFact]:
        return [
            fact
            for fact in self.facts
            if fact.assertion == "explicit"
            and fact.trust == "trusted"
            and fact.field in _VISIBLE_SCRIPT_FACT_FIELDS
        ]


class CharacterDesignBrief(BaseModel):
    kind: Literal["creative_design"] = "creative_design"
    character_id: str = Field(min_length=1)
    era_region: str = ""
    occupation_class: str = ""
    temperament: str = ""
    project_style: str = ""
    design_goal: str = ""


class CharacterDesignProposal(BaseModel):
    proposal_id: str = Field(min_length=1)
    kind: Literal["creative_design"] = "creative_design"
    title: str = Field(min_length=1)
    rationale: str = ""
    recommended: bool = False
    face_shape: str | None = None
    facial_features: list[str] = Field(default_factory=list)
    hair_style: str | None = None
    body_type: str | None = None
    distinctive_features: list[str] = Field(default_factory=list)
    identity_anchors: list[str] = Field(default_factory=list)
    asymmetry_detail: str = ""
    quality_issues: list[str] = Field(default_factory=list)
    outfit_states: dict[str, str] = Field(default_factory=dict)


class CharacterVisualBible(BaseModel):
    character_id: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    status: Literal["draft", "confirmed", "superseded"] = "draft"
    face_shape: str | None = None
    facial_features: list[str] = Field(default_factory=list)
    hair_style: str | None = None
    body_type: str | None = None
    distinctive_features: list[str] = Field(default_factory=list)
    outfit_states: dict[str, str] = Field(default_factory=dict)
    identity_anchors: list[str] = Field(default_factory=list)
    source_fact_ids: list[str] = Field(default_factory=list)
    confirmed_by: str | None = None

    @model_validator(mode="after")
    def validate_confirmation(self) -> "CharacterVisualBible":
        if self.status != "confirmed":
            return self
        if not str(self.confirmed_by or "").strip():
            raise ValueError("confirmed visual bible requires confirmed_by")
        if not str(self.face_shape or "").strip():
            raise ValueError("confirmed visual bible requires face shape")
        if len({value.strip() for value in self.facial_features if value.strip()}) < 2:
            raise ValueError("confirmed visual bible requires at least two facial features")
        if len({value.strip() for value in self.identity_anchors if value.strip()}) < 3:
            raise ValueError("confirmed visual bible requires at least three identity anchors")
        return self


class LegacyVisualField(BaseModel):
    field: str
    value: str
    source: str | None = None
    trust: Literal["legacy_untrusted"] = "legacy_untrusted"
    allowed_for_generation: Literal[False] = False


class CharacterVisualWorkspace(BaseModel):
    """Editable character-visual state before prompt compilation."""

    character_id: str = Field(min_length=1)
    profile: CharacterNarrativeProfile
    design_proposals: list[CharacterDesignProposal] = Field(default_factory=list)
    selected_proposal_id: str | None = None
    visual_bible: CharacterVisualBible | None = None
    legacy_fields: list[LegacyVisualField] = Field(default_factory=list)


def classify_legacy_visual_field(
    *, field: str, value: str, source: str | None
) -> LegacyVisualField:
    """Quarantine legacy visual text whose provenance cannot be proven.

    Trust must be established by an explicit migration or human confirmation; a
    plausible-looking prompt is not evidence of provenance.
    """

    return LegacyVisualField(field=field, value=value, source=source)
