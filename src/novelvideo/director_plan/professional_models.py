"""Bounded, source-locked records for professional director-plan contributions."""

from __future__ import annotations

from typing import Any, Literal, Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .cinematography import PropBlocking, SubjectBlocking
from .creative_contract import LightingDecision, PerformanceBeat

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ProfessionalModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ProfessionalAssignment(ProfessionalModel):
    """A director-selected task scoped to exact groups and shots."""

    role_id: Literal[
        "builtin:storyboard-artist",
        "builtin:blocking-designer",
        "builtin:cinematographer",
        "builtin:lighting-designer",
        "builtin:expression-designer",
        "builtin:art-asset-designer",
    ]
    group_id: Text
    shot_ids: tuple[Text, ...] = Field(min_length=1, max_length=100)
    task: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class ProfessionalRunReceipt(ProfessionalModel):
    """Durable evidence for one actual specialist model call and its merge."""

    run_id: Text
    role_id: Literal[
        "builtin:storyboard-artist",
        "builtin:blocking-designer",
        "builtin:cinematographer",
        "builtin:lighting-designer",
        "builtin:expression-designer",
        "builtin:art-asset-designer",
    ]
    profile_revision: int = Field(ge=1)
    profile_name: Text
    profile_source: dict[str, Any]
    group_id: Text
    shot_ids: tuple[Text, ...] = Field(min_length=1)
    task: Text
    reason: Text
    status: Literal["pending", "completed", "failed", "unknown"]
    summary: Text | None = None
    model: Text
    source_script_hash: Text
    semantic_revision_id: Text | None = None
    output: dict[str, Any] | None = None
    merged_fields: tuple[Text, ...] = ()
    error_code: Text | None = None


class ShotIntentContribution(ProfessionalModel):
    shot_id: Text
    narrative_purpose: Text
    audience_attention: Text
    emotional_effect: Text
    continuity_strategy: Text


class StoryboardProfessionalOutput(ProfessionalModel):
    summary: Text
    contributions: tuple[ShotIntentContribution, ...] = Field(min_length=1, max_length=100)


class CinematographyContribution(ProfessionalModel):
    shot_id: Text
    shot_size: Text
    camera_angle: Text
    composition: Text
    camera_motion: Text
    camera_side: Text
    transition_intent: Text


class CinematographyProfessionalOutput(ProfessionalModel):
    summary: Text
    contributions: tuple[CinematographyContribution, ...] = Field(min_length=1, max_length=100)


class BlockingContribution(ProfessionalModel):
    shot_id: Text
    axis: Text
    screen_direction: Text
    subjects: tuple[SubjectBlocking, ...] = ()
    props: tuple[PropBlocking, ...] = ()


class BlockingProfessionalOutput(ProfessionalModel):
    summary: Text
    contributions: tuple[BlockingContribution, ...] = Field(min_length=1, max_length=100)


class LightingContribution(ProfessionalModel):
    shot_id: Text
    lighting: LightingDecision


class LightingProfessionalOutput(ProfessionalModel):
    summary: Text
    contributions: tuple[LightingContribution, ...] = Field(min_length=1, max_length=100)


class PerformanceCharacterContribution(ProfessionalModel):
    subject_id: Text
    start_behavior: Text
    end_behavior: Text
    goal: Text
    obstacle: Text
    tactics: Text
    visible_behavior: Text
    gaze: Text
    contact: Text
    held_objects: Text
    weight_and_inertia: Text
    beats: tuple[PerformanceBeat, ...] = Field(min_length=1)


class PerformanceContribution(ProfessionalModel):
    shot_id: Text
    characters: tuple[PerformanceCharacterContribution, ...] = Field(min_length=1)


class PerformanceProfessionalOutput(ProfessionalModel):
    summary: Text
    contributions: tuple[PerformanceContribution, ...] = Field(min_length=1, max_length=100)


class AssetDesignNote(ProfessionalModel):
    entity_key: Text
    design_notes: Text


class ArtContribution(ProfessionalModel):
    shot_id: Text
    asset_requirements: tuple[AssetDesignNote, ...] = Field(min_length=1)


class ArtProfessionalOutput(ProfessionalModel):
    summary: Text
    contributions: tuple[ArtContribution, ...] = Field(min_length=1, max_length=100)
