from enum import StrEnum

from pydantic import BaseModel, Field


class H3Mode(StrEnum):
    T2VA = "t2va"
    I2VA = "i2va"
    L2VA = "l2va"
    FL2VA = "fl2va"
    REF2VA = "ref2va"


class MotionSpec(BaseModel):
    action: str = Field(min_length=1)
    dialogue: str | None = None
    soundscape: str | None = None
    music: str | None = None
    subject_definitions: tuple[str, ...] = ()
    summary: str | None = None
    retention_analysis: str | None = None
