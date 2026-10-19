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


def split_segments(
    rows,
    group,
    *,
    merge_adjacent: bool = False,
    limit_seconds: float = 15.0,
) -> SegmentPlan:
    """Split ordered storyboard rows into H3 segment drafts.

    Default is one cell per segment (spec R1). Adjacent rows merge only when the
    caller opts in and the combined duration stays within the attempt limit.
    Segment IDs are renumbered in row order so reordering yields stable output.
    """
    group_id = str(group["id"])
    segments: list[SegmentDraft] = []
    buffer: list[dict] = []

    def flush() -> None:
        if not buffer:
            return
        first, last = buffer[0], buffer[-1]
        segments.append(
            SegmentDraft(
                segment_id=f"{group_id}-S{len(segments) + 1:02d}",
                source_rows=[int(row["shot_no"]) for row in buffer],
                duration_seconds=float(sum(float(row["duration"]) for row in buffer)),
                frame_mode="first",
                first_frame=first.get("cell_image"),
                last_frame=last.get("cell_image") if len(buffer) > 1 else None,
                panel_spec=dict(first.get("director", {}).get("panel_spec") or {}),
                technique_intent=str(first.get("director", {}).get("technique_intent") or ""),
            )
        )
        buffer.clear()

    for row in rows:
        candidate = sum(float(item["duration"]) for item in buffer) + float(row["duration"])
        if buffer and (not merge_adjacent or candidate > limit_seconds):
            flush()
        buffer.append(row)
    flush()
    return SegmentPlan(group_id=group_id, segments=segments)
