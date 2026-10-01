"""Validated, provider-independent white-model action timeline."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

Vector = tuple[FiniteFloat, FiniteFloat, FiniteFloat]


class Item(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1, max_length=100)


class Actor(Item):
    name: str = Field(min_length=1, max_length=200)
    humanoid: bool
    position: Vector
    yaw: FiniteFloat
    characterRef: str | None = None


class Clip(Item):
    actorId: str
    action: Literal['walk', 'run', 'turn', 'sit']
    start: Annotated[FiniteFloat, Field(ge=0, le=3600)]
    duration: Annotated[FiniteFloat, Field(ge=0.1, le=3600)]
    target: Vector
    yaw: FiniteFloat


class CameraKey(Item):
    time: Annotated[FiniteFloat, Field(ge=0, le=3600)]
    position: Vector
    target: Vector


class Light(BaseModel):
    yaw: FiniteFloat
    intensity: Annotated[FiniteFloat, Field(ge=0, le=20)]


class Prop(Item):
    position: Vector
    scale: Vector


class Source(BaseModel):
    type: Literal['canvas', 'narrative_group', 'episode']
    id: str = Field(min_length=1, max_length=200)
    revision: str | None = None


class PrevisScene(BaseModel):
    model_config = ConfigDict(extra='forbid')
    actors: list[Actor] = Field(max_length=100)
    clips: list[Clip] = Field(max_length=2000)
    camera: list[CameraKey] = Field(min_length=1, max_length=2000)
    light: Light
    props: list[Prop] = Field(max_length=500)
    reference: str | None = Field(default=None, max_length=1_500_000)
    source: Source | None = None

    @model_validator(mode='after')
    def validate_timeline(self):
        for items in (self.actors, self.clips, self.camera, self.props):
            if len({item.id for item in items}) != len(items):
                raise ValueError('对象标识重复')
        if len({key.time for key in self.camera}) != len(self.camera):
            raise ValueError('摄影机关键帧时间重复')
        actors = {actor.id: actor for actor in self.actors}
        for clip in self.clips:
            if clip.actorId not in actors:
                raise ValueError('动作引用的演员不存在')
            if not actors[clip.actorId].humanoid:
                raise ValueError('非人形演员不能使用人形动作')
            if clip.start + clip.duration > 3600:
                raise ValueError('预演不能超过一小时')
        for actor in self.actors:
            clips = sorted((c for c in self.clips if c.actorId == actor.id), key=lambda c: c.start)
            for left, right in zip(clips, clips[1:]):
                if left.start + left.duration > right.start + 0.00001:
                    raise ValueError('同一演员的主动作不能重叠')
        if any(any(v <= 0 or v > 1000 for v in p.scale) for p in self.props):
            raise ValueError('物件尺寸无效')
        return self
