"""Stable voice identity models."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class VoiceSpecError(ValueError):
    """Raised when a voice specification is unsafe or contradictory."""


class VoiceProfileStatus(StrEnum):
    DRAFT = "draft"
    CANDIDATE = "candidate"
    APPROVED = "approved"
    RETIRED = "retired"


class VoiceSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    age_impression: str = ""
    pitch: str = ""
    texture: str = ""
    resonance: str = ""
    articulation: str = ""
    pace: str = ""
    accent: str = ""
    emotional_baseline: str = ""
    negative_constraints: tuple[str, ...] = ()


__all__ = ["VoiceProfileStatus", "VoiceSpec", "VoiceSpecError"]
