"""C 切片（执行导演）段草案接口契约。

本模块只冻结字段名与函数签名，分段逻辑由 C 切片实现。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FrameMode = Literal["first", "first_last"]


class SegmentReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["character", "scene", "prop"]
    asset_id: str
    image: str


class SegmentDialogue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker: str = ""
    text: str
    mode: Literal["voiceover", "on_camera"] = "on_camera"


class TechniqueRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    version: int


class CaseRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    source_id: str = ""
    provenance: str = ""


class SegmentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_id: str
    source_rows: list[int] = Field(default_factory=list)
    duration_seconds: float = Field(gt=0)
    frame_mode: FrameMode = "first"
    first_frame: str | None = None
    last_frame: str | None = None
    references: list[SegmentReference] = Field(default_factory=list)
    dialogue: list[SegmentDialogue] = Field(default_factory=list)
    panel_spec: dict = Field(default_factory=dict)
    technique_intent: str = ""
    technique: TechniqueRef | None = None
    case_reference: CaseRef | None = None


class SegmentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: str
    segments: list[SegmentDraft] = Field(default_factory=list)


def split_segments(rows, group) -> SegmentPlan:
    raise NotImplementedError("C slice implements split_segments")
