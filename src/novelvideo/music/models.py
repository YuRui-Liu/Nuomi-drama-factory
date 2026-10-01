"""Versioned music desk wire contracts. All times are integer milliseconds."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Ms = Annotated[int, Field(ge=0, strict=True)]
Id = Annotated[str, Field(min_length=1, max_length=128)]
Gain = Annotated[float, Field(ge=-60, le=6)]


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Interval(Model):
    startMs: Ms
    endMs: Ms

    @model_validator(mode='after')
    def ordered(self):
        if self.endMs <= self.startMs:
            raise ValueError('时间区间终点必须晚于起点')
        return self


class MusicClip(Model):
    id: Id
    assetVersionId: Id
    startMs: Ms
    sourceInMs: Ms
    lengthMs: Annotated[int, Field(gt=0, strict=True)]
    gainDb: Gain = 0
    fadeInMs: Ms = 0
    fadeOutMs: Ms = 0
    loop: Interval | None = None

    @model_validator(mode='after')
    def fades(self):
        if self.fadeInMs + self.fadeOutMs > self.lengthMs:
            raise ValueError('淡入淡出超过片段长度')
        return self


class MusicTrack(Model):
    id: Id
    name: str = Field(min_length=1, max_length=100)
    gainDb: Gain = 0
    muted: bool = False
    solo: bool = False
    clips: list[MusicClip] = Field(default_factory=list, max_length=200)


class Source(Model):
    assetVersionId: Id
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    durationMs: Annotated[int, Field(gt=0, le=86400000, strict=True)]


class Original(Model):
    muted: bool = False
    gainDb: Gain = 0


class Ducking(Model):
    enabled: bool = False
    gainDb: Annotated[float, Field(ge=-60, le=0)] = -12
    attackMs: Ms = 150
    releaseMs: Ms = 300
    intervals: list[Interval] = Field(default_factory=list, max_length=1000)


class MusicPlan(Model):
    schemaVersion: Literal[1] = 1
    revision: Ms = 0
    source: Source
    original: Original = Field(default_factory=Original)
    ducking: Ducking = Field(default_factory=Ducking)
    tracks: list[MusicTrack] = Field(default_factory=list, max_length=32)

    @model_validator(mode='after')
    def timeline(self):
        ids: set[str] = set()
        for track in self.tracks:
            if track.id in ids:
                raise ValueError('轨道或片段 ID 重复')
            ids.add(track.id)
            end = 0
            for clip in sorted(track.clips, key=lambda c: c.startMs):
                if clip.id in ids:
                    raise ValueError('轨道或片段 ID 重复')
                ids.add(clip.id)
                if clip.startMs < end:
                    raise ValueError('同轨片段重叠，请替换选区或新增轨道')
                end = clip.startMs + clip.lengthMs
                if end > self.source.durationMs:
                    raise ValueError('配乐超出成片范围')
        for interval in self.ducking.intervals:
            if interval.endMs > self.source.durationMs:
                raise ValueError('对白区间超出成片')
        return self


def validate_sources(plan: MusicPlan, durations: dict[str, int]) -> None:
    for track in plan.tracks:
        for clip in track.clips:
            duration = durations.get(clip.assetVersionId)
            if duration is None:
                raise ValueError('配乐来源无效')
            if clip.loop:
                if clip.loop.endMs > duration or not clip.loop.startMs <= clip.sourceInMs < clip.loop.endMs:
                    raise ValueError('循环区间或入点无效')
            elif clip.sourceInMs + clip.lengthMs > duration:
                raise ValueError('音乐长度不足，请缩短片段或显式设置循环')
