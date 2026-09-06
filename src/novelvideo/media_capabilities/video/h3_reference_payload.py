"""Pure compiler for MiniMax H3 global-reference timeline payloads."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from .h3_size_settings import resolve_h3_size_setting
from .h3_timeline import H3Timeline


H3_REFERENCE_TASK_TYPE = "r2v — 参考主体生视频(Reference to Video)"
H3_REFERENCE_TIMELINE_MODE = "prompt_batch"
_REFERENCE_NUMBERING = re.compile(r"<\s*(?:subject|picture)\s+\d+\s*>", re.IGNORECASE)
_SHOT_ORIGIN = re.compile(r"\(?\s*from\s+shot\s+\d+\s*\)?", re.IGNORECASE)
_ASPECT_LABELS = {
    "9:16": "9:16 (竖版宽屏)",
    "16:9": "16:9 (宽屏)",
}


class H3GlobalReference(BaseModel):
    """One ordered reference asset after upload, before payload compilation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference_id: str
    source_kind: str
    label: str
    subject_description: str
    uploaded_url: str
    sha256: str

    @field_validator(
        "reference_id",
        "source_kind",
        "label",
        "uploaded_url",
        "sha256",
        mode="before",
    )
    @classmethod
    def trim_required_text(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("value must not be blank")
        return value

    @field_validator("subject_description", mode="before")
    @classmethod
    def validate_subject_description(cls, value: object) -> object:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("subject_description must not be blank")
        normalized = value.strip()
        if "\n" in normalized or "\r" in normalized:
            raise ValueError("subject_description must be a single line")
        if _REFERENCE_NUMBERING.search(normalized):
            raise ValueError("subject_description must not define Subject/Picture numbers")
        if _SHOT_ORIGIN.search(normalized):
            raise ValueError("subject_description must not define a Shot origin")
        return normalized


def _output_settings(aspect_ratio: str, resolution: str) -> dict[str, object]:
    setting = resolve_h3_size_setting(resolution, aspect_ratio)
    return {
        "mode": "fixed",
        "aspectRatio": _ASPECT_LABELS[setting.aspect_ratio],
        "megapixels": setting.megapixels,
        "multiple": setting.multiple,
        "width": setting.width,
        "height": setting.height,
        "longEdge": setting.long_edge,
        "refMaxSize": setting.ref_max_size,
        "maxExportFrames": 0,
        "exportMode": "all",
        "audioMode": "generate",
        "continuityEnabled": False,
        "continuityOverlapFrames": 5,
    }


def _image(value: object) -> dict[str, object] | None:
    if isinstance(value, Mapping):
        return dict(value)
    return {"imageFile": value} if value else None


def _normalize_references(
    references: Iterable[H3GlobalReference],
    *,
    max_references: int,
) -> tuple[H3GlobalReference, ...]:
    if isinstance(max_references, bool) or not isinstance(max_references, int):
        raise ValueError("max_references must be an integer from 1 to 10")
    if not 1 <= max_references <= 10:
        raise ValueError("max_references must be from 1 to 10")

    normalized = tuple(references)
    if not normalized:
        raise ValueError("at least one reference is required")
    if len(normalized) > max_references:
        raise ValueError(f"at most {max_references} reference(s) are allowed")

    reference_ids: set[str] = set()
    uploaded_urls: set[str] = set()
    for reference in normalized:
        if not isinstance(reference, H3GlobalReference):
            raise TypeError("references must contain H3GlobalReference values")
        if reference.reference_id in reference_ids:
            raise ValueError(f"duplicate reference_id: {reference.reference_id}")
        if reference.uploaded_url in uploaded_urls:
            raise ValueError(f"duplicate uploaded_url: {reference.uploaded_url}")
        reference_ids.add(reference.reference_id)
        uploaded_urls.add(reference.uploaded_url)
    return normalized


def _segment_mode(
    requested_mode: Literal["auto", "i2va", "fl2va"],
    *,
    segment_id: str,
    last_frame: object,
) -> str:
    if requested_mode == "auto":
        return "Ref-FL2V" if last_frame else "Ref-I2V"
    if requested_mode == "i2va":
        return "Ref-I2V"
    if not last_frame:
        raise ValueError(f"segment {segment_id} requires a last frame in fl2va mode")
    return "Ref-FL2V"


def build_h3_reference_timeline_payload(
    timeline: H3Timeline,
    ordered_references: Iterable[H3GlobalReference],
    *,
    max_references: int,
    mode: Literal["auto", "i2va", "fl2va"] = "auto",
    uploaded_frames: Mapping[str, object] | None = None,
    aspect_ratio: str = "9:16",
    resolution: str = "720p",
    output_settings: Mapping[str, object] | None = None,
) -> str:
    """Compile a version-5 reference timeline without mutating request models.

    The source workflow proves the pure R2V envelope. Mixed Ref-I2V/Ref-FL2V
    segment values are intentionally a local compilation contract until a paid
    provider smoke test establishes remote hybrid compatibility.
    """
    references = _normalize_references(
        ordered_references,
        max_references=max_references,
    )
    requested_mode = str(mode).strip().lower()
    if requested_mode not in {"auto", "i2va", "fl2va"}:
        raise ValueError("mode must be auto, i2va, or fl2va")

    frame_uploads = uploaded_frames or {}
    shots: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    for index, entry in enumerate(timeline.entries):
        source = entry.segment
        if not source.first_frame:
            raise ValueError(f"segment {source.segment_id} requires a first frame")
        first = _image(frame_uploads.get(source.first_frame, source.first_frame))
        last = _image(
            frame_uploads.get(source.last_frame, source.last_frame)
            if source.last_frame
            else None
        )
        segment_mode = _segment_mode(
            requested_mode,
            segment_id=source.segment_id,
            last_frame=last,
        )
        shots.append(
            {
                "id": source.segment_id,
                "durationSec": source.duration_seconds,
                "prompt": source.prompt,
                "negativePrompt": "",
                "continuityFromPrev": index > 0,
                "startImage": first,
                "endImage": last,
            }
        )
        segments.append(
            {
                "id": source.segment_id,
                "start": entry.start_frame,
                "length": entry.frame_count,
                "frameCount": entry.frame_count,
                "durationSec": source.duration_seconds,
                "prompt": source.prompt,
                "negativePrompt": "",
                "continuityFromPrev": index > 0,
                "isStartFrame": True,
                "isEndFrame": last is not None,
                "genImage": first,
                "endImage": last,
                "taskType": segment_mode,
                "refs": [],
            }
        )

    output = (
        dict(output_settings)
        if output_settings is not None
        else _output_settings(aspect_ratio, resolution)
    )
    keyframes: list[dict[str, Any]] = []
    for segment in segments:
        half = segment["frameCount"] // 2
        keyframes.append(
            {
                "id": f"{segment['id']}_s",
                "imageFile": segment["genImage"]["imageFile"],
                "start": segment["start"],
                "length": half,
                "frameCount": half,
                "durationSec": segment["durationSec"],
                "prompt": segment["prompt"],
                "negativePrompt": segment["negativePrompt"],
                "isStartFrame": True,
                "isEndFrame": False,
            }
        )
        if segment["isEndFrame"]:
            keyframes.append(
                {
                    "id": f"{segment['id']}_e",
                    "imageFile": segment["endImage"]["imageFile"],
                    "start": segment["start"] + half,
                    "length": segment["frameCount"] - half,
                    "frameCount": segment["frameCount"] - half,
                    "durationSec": segment["durationSec"],
                    "prompt": "",
                    "negativePrompt": segment["negativePrompt"],
                    "isStartFrame": False,
                    "isEndFrame": True,
                }
            )

    subject_definitions = "subject_definitions:\n" + "\n".join(
        f"<Subject {index}> is {reference.subject_description} "
        f"from <Picture {index}>"
        for index, reference in enumerate(references, start=1)
    )
    payload = {
        "version": 5,
        "editMode": "segment",
        "totalFrames": timeline.total_frames,
        "frameRate": timeline.fps,
        "video": {
            "fileName": "",
            "videoFile": "",
            "subfolder": "",
            "type": "input",
            "frames": [],
            "frameMap": [],
            "sourceFrameCount": timeline.total_frames * 2,
            "deletedSourceRanges": [],
        },
        "videoClips": [],
        "global": {
            "taskType": H3_REFERENCE_TASK_TYPE,
            "prompt": subject_definitions,
            "refs": [
                {
                    "index": index,
                    "imageFile": reference.uploaded_url,
                    "fileName": "",
                    "type": "input",
                    "subfolder": "",
                }
                for index, reference in enumerate(references)
            ],
            "referenceVideo": {
                "videoFile": "",
                "fileName": "",
                "type": "input",
                "subfolder": "",
            },
            "continuousReference": len(segments) > 1,
            "genImage": {"imageFile": ""},
            "sourceWidth": output["width"],
            "sourceHeight": output["height"],
            "refAudios": [],
            "refVideos": [],
            "commonEnabled": True,
            "commonCollapsed": True,
        },
        "output": output,
        "runSelectEnabled": False,
        "runSelection": [],
        "segments": segments,
        "timelineMode": H3_REFERENCE_TIMELINE_MODE,
        "width": output["width"],
        "height": output["height"],
        "refMaxSize": output["longEdge"],
        "durationSec": sum(entry.segment.duration_seconds for entry in timeline.entries),
        "shots": shots,
        "gen": {"defaultFrameCount": timeline.entries[0].frame_count},
        "keyframes": keyframes,
        "liveTaePreview": True,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


__all__ = [
    "H3GlobalReference",
    "H3_REFERENCE_TASK_TYPE",
    "H3_REFERENCE_TIMELINE_MODE",
    "build_h3_reference_timeline_payload",
]
