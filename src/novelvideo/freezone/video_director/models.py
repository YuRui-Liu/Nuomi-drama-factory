"""Versioned, immutable Director editor and attempt snapshots."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class DirectorSegment(_FrozenModel):
    id: str
    prompt: str = ""
    duration_seconds: float
    first_frame: DirectorImage | None = None
    last_frame: DirectorImage | None = None

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


class DirectorAttempt(_FrozenModel):
    id: str
    revision: int = Field(ge=0)
    snapshot: DirectorDraft
    stage: str
    optimized_segments: tuple[DirectorSegment, ...] = ()
    provider_task_id: str | None = None
    result_url: str | None = None
    error: str | None = None
    failed_stage: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


__all__ = ["DirectorImage", "DirectorSegment", "DirectorDraft", "DirectorAttempt"]
