"""Versioned, immutable Director editor and attempt snapshots."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from novelvideo.media_capabilities.video.h3_wire import H3BaseWire, H3ReferenceWire
from .techniques import TechniqueCard


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DirectorImage(_FrozenModel):
    image_id: str
    url: str
    asset_id: str | None = None
    character_id: str | None = None
    variant_id: str | None = None
    variant_label: str | None = None
    asset_kind: str | None = None
    sha256: str | None = None


class TechniqueSelection(_FrozenModel):
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)


class FrozenTechniqueProjection(_FrozenModel):
    id: str
    version: str
    content_hash: str
    intent: str
    action_beats: tuple[str, ...]
    performance: str
    camera: str
    ending_composition: str
    avoid: tuple[str, ...]


class FrozenTechnique(_FrozenModel):
    card: TechniqueCard
    projection: FrozenTechniqueProjection


class DirectorSegment(_FrozenModel):
    id: str
    prompt: str = ""
    duration_seconds: float
    first_frame: DirectorImage | None = None
    last_frame: DirectorImage | None = None
    technique: TechniqueSelection | None = None

    @field_validator("duration_seconds")
    @classmethod
    def positive_finite_duration(cls, value: float) -> float:
        if not math.isfinite(value) or value <= 0:
            raise ValueError("duration_seconds must be positive and finite")
        return value


class DirectorDraft(_FrozenModel):
    schema_version: Literal[1] = 1
    revision: int = Field(ge=0)
    model_id: str = "minimax-h3"
    aspect_ratio: str
    resolution: str
    references: tuple[DirectorImage, ...] = ()
    segments: tuple[DirectorSegment, ...] = ()

    @model_validator(mode="after")
    def unique_segment_ids(self) -> "DirectorDraft":
        seen: set[str] = set()
        for segment in self.segments:
            if segment.id in seen:
                raise ValueError(f"duplicate segment id: {segment.id}")
            seen.add(segment.id)
        return self


class CanvasBaseWire(H3BaseWire):
    """Canvas timeline can align beyond the legacy wire's 15 second ceiling."""

    duration_seconds: float = Field(gt=0, allow_inf_nan=False)


class CanvasReferenceWire(H3ReferenceWire):
    duration_seconds: float = Field(gt=0, allow_inf_nan=False)


class OptimizedSegment(_FrozenModel):
    segment_id: str
    mode: str
    requested_duration_seconds: float
    duration_seconds: float
    frames: int = Field(gt=0)
    wire: CanvasBaseWire | CanvasReferenceWire
    prompt: str


class OptimizedDirector(_FrozenModel):
    revision: int = Field(ge=0)
    route: Literal["h3", "h3_ref"]
    profile_id: str
    profile_version: int
    optimized_at: datetime
    segments: tuple[OptimizedSegment, ...]


class DirectorAttempt(_FrozenModel):
    id: str
    revision: int = Field(ge=0)
    snapshot: DirectorDraft
    stage: str
    optimized_segments: tuple[OptimizedSegment, ...] = ()
    provider_task_id: str | None = None
    result_url: str | None = None
    error: str | None = None
    failed_stage: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


__all__ = ["DirectorImage", "TechniqueSelection", "FrozenTechniqueProjection",
           "FrozenTechnique", "DirectorSegment", "DirectorDraft", "DirectorAttempt",
           "CanvasBaseWire", "CanvasReferenceWire", "OptimizedSegment", "OptimizedDirector"]
