"""Immutable DTOs for the narrative-group media workflow."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
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


class _FrozenStringMapping(Mapping[str, str]):
    __slots__ = ("_items",)

    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        object.__setattr__(
            self,
            "_items",
            tuple(() if values is None else values.items()),
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("mapping is immutable")

    def __delattr__(self, name: str) -> None:
        raise TypeError("mapping is immutable")

    def __getitem__(self, key: str) -> str:
        for item_key, value in self._items:
            if item_key == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Mapping) and dict(self) == dict(other)

    def __deepcopy__(self, memo: dict[int, object]) -> _FrozenStringMapping:
        return self


@dataclass(frozen=True)
class VideoSettings:
    workflow_id: str = "runninghub:minimax-h3"
    revision: int = 0
    overrides: Mapping[str, str] = field(default_factory=_FrozenStringMapping)

    def __post_init__(self) -> None:
        if not isinstance(self.workflow_id, str):
            raise TypeError("workflow_id must be a string")
        if not isinstance(self.overrides, Mapping):
            raise TypeError("overrides must be a mapping")
        copied: dict[str, str] = {}
        for key, value in self.overrides.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise TypeError("override keys and values must be strings")
            copied[key] = value
        object.__setattr__(self, "overrides", _FrozenStringMapping(copied))

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "revision": self.revision,
            "overrides": dict(self.overrides),
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
    requested_image_size: str = ""
    requested_pixel_size: str = ""
    actual_pixel_size: str = ""
    resolution_warning: str = ""
    workflow_parameters: dict[str, str] = field(default_factory=dict)
    provider_parameters: dict[str, Any] = field(default_factory=dict)
    actual_output: dict[str, int] = field(default_factory=dict)
    cleanup_reports: tuple[dict[str, Any], ...] = ()
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
    video_settings: VideoSettings = field(default_factory=VideoSettings)
    stages: dict[StageName, GroupStageState] = field(default_factory=_default_stages)
    errors: tuple[dict, ...] = ()
    source_span_ids: tuple[str, ...] = ()
    shot_ids: tuple[str, ...] = ()
    objective: str = ""
    visible_turn: str = ""
    director_revision_id: str = ""

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
        result["video_settings"] = self.video_settings.to_dict()
        return result
