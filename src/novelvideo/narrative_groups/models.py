"""Immutable DTOs for the narrative-group media workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

StageName = Literal["sketch", "render", "video"]
StageStatus = Literal[
    "pending",
    "queued",
    "running",
    "review",
    "completed",
    "partial_failure",
    "failed",
]


@dataclass(frozen=True)
class GridLayout:
    rows: int
    columns: int
    capacity: int

    @property
    def shape(self) -> tuple[int, int]:
        return (self.rows, self.columns)


@dataclass(frozen=True)
class CellMapping:
    cell: int
    beat_id: str


@dataclass(frozen=True)
class VideoPlanUnit:
    id: str
    beat_ids: tuple[str, ...]
    mode: Literal["i2va", "fl2va"]
    duration_seconds: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["beat_ids"] = list(self.beat_ids)
        return result


@dataclass(frozen=True)
class VideoPlan:
    revision: int = 0
    source: Literal["recommended", "manual"] = "recommended"
    units: tuple[VideoPlanUnit, ...] = ()
    total_duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "source": self.source,
            "units": [unit.to_dict() for unit in self.units],
            "total_duration_seconds": self.total_duration_seconds,
        }


@dataclass(frozen=True)
class GroupStageState:
    status: StageStatus = "pending"
    revision: int = 0
    grid_asset: str = ""
    cell_assets: tuple[dict, ...] = ()
    # A video group is one physical H3 director output containing logical shots.
    video_asset: str = ""
    manifest_asset: str = ""
    original_audio_path: str = ""
    dialogue_stem_path: str = ""
    ambience_stem_path: str = ""
    dialogue_stem_status: str = "not_requested"
    ambience_stem_status: str = "not_requested"
    error: str = ""
    actual_provider: str = ""
    actual_model: str = ""
    actual_mode: str = ""
    source_sketch_revision: int = 0
    constraint_mode: str = ""
    created_at: str = ""
    revision_history: tuple[dict[str, Any], ...] = ()


def _default_stages() -> dict[StageName, GroupStageState]:
    return {
        "sketch": GroupStageState(),
        "render": GroupStageState(),
        "video": GroupStageState(),
    }


@dataclass(frozen=True)
class NarrativeGroup:
    id: str
    ordinal: int
    beat_ids: tuple[str, ...]
    layout: GridLayout
    cell_to_beat: tuple[CellMapping, ...]
    video_plan: VideoPlan = field(default_factory=VideoPlan)
    stages: dict[StageName, GroupStageState] = field(default_factory=_default_stages)
    errors: tuple[dict, ...] = ()

    @property
    def video_inputs(self) -> tuple[dict[str, Any], ...]:
        render = self.stages.get("render", GroupStageState())
        by_beat = {str(item.get("beat_id")): item for item in render.cell_assets}
        return tuple(
            {
                "beat_id": beat_id,
                "has_first_frame": bool(
                    by_beat.get(beat_id, {}).get("first_frame")
                    or by_beat.get(beat_id, {}).get("path")
                ),
                "has_last_frame": bool(by_beat.get(beat_id, {}).get("last_frame")),
                "actual_provider": (
                    by_beat.get(beat_id, {}).get("actual_provider")
                    or render.actual_provider
                    or None
                ),
                "actual_model": (
                    by_beat.get(beat_id, {}).get("actual_model")
                    or render.actual_model
                    or None
                ),
                "actual_mode": (
                    by_beat.get(beat_id, {}).get("actual_mode")
                    or render.actual_mode
                    or None
                ),
            }
            for beat_id in self.beat_ids
        )

    def to_dict(self) -> dict:
        result = asdict(self)
        result["video_inputs"] = list(self.video_inputs)
        result["video_plan"] = self.video_plan.to_dict()
        return result
