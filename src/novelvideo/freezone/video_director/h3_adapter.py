"""Compile optimized canvas segments into a real H3 Director timeline envelope."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping

from novelvideo.media_capabilities.video.h3_reference_payload import (
    H3_REFERENCE_TASK_TYPE, H3_REFERENCE_TIMELINE_MODE,
)
from novelvideo.media_capabilities.video.h3_timeline import (
    H3CompiledTimeline, H3DirectorSegment, H3TimelineEntry,
)
from novelvideo.media_capabilities.video.runtime import (
    _director_semantic_values, _director_timeline_payload,
)

from .capabilities import validate_generation
from .models import DirectorDraft, OptimizedDirector
from .prepared_validation import validate_optimized_director


@dataclass(frozen=True, slots=True)
class DirectorPayload:
    route: str
    timeline_data: str
    semantic_values: dict[str, object]


def _uploaded_image(value: object, image_id: str) -> dict[str, object]:
    if isinstance(value, str):
        value = {"imageFile": value}
    if not isinstance(value, dict) or not isinstance(value.get("imageFile"), str) or not value["imageFile"].strip():
        raise ValueError(f"invalid uploaded image for {image_id}")
    return dict(value)


def compile_director_payload(
    draft: DirectorDraft,
    optimized: OptimizedDirector,
    uploaded_images: Mapping[str, object],
    *,
    reference_limit: int = 5,
) -> DirectorPayload:
    """Compile one immutable attempt; reject stale or partial optimizations."""
    validation = validate_generation(draft, reference_limit=reference_limit)
    validate_optimized_director(draft, optimized, validation)
    entries = []
    for index, (source, result, aligned) in enumerate(zip(
        draft.segments, optimized.segments, validation.timeline, strict=True
    )):
        entries.append(H3TimelineEntry(
            segment=H3DirectorSegment(
                segment_id=source.id, beat_number=index + 1,
                prompt=result.prompt, duration_seconds=aligned.duration_seconds,
                first_frame=source.first_frame.image_id if source.first_frame else None,
                last_frame=source.last_frame.image_id if source.last_frame else None,
            ), start_frame=aligned.start_frame, frame_count=aligned.frames,
        ))
    timeline = H3CompiledTimeline(entries=tuple(entries), total_frames=validation.total_frames)

    frame_ids = [frame.image_id for segment in draft.segments
                 for frame in (segment.first_frame, segment.last_frame) if frame is not None]
    upload_ids = list(dict.fromkeys((*validation.reference_image_ids, *frame_ids)))
    try:
        normalized_uploads = {image_id: _uploaded_image(uploaded_images[image_id], image_id)
                              for image_id in upload_ids}
    except KeyError as exc:
        raise ValueError(f"uploaded image is missing: {exc.args[0]}") from exc
    timeline_data = _director_timeline_payload(
        timeline, normalized_uploads, aspect_ratio=draft.aspect_ratio,
        resolution=draft.resolution,
    )
    if validation.route == "h3_ref":
        data = json.loads(timeline_data)
        data["global"]["taskType"] = H3_REFERENCE_TASK_TYPE
        data["global"]["prompt"] = (optimized.segments[0].prompt if len(optimized.segments) == 1
                                    else data["global"]["prompt"])
        data["global"]["refs"] = [
            {"index": index, "imageFile": normalized_uploads[image_id]["imageFile"],
             "fileName": "", "type": "input", "subfolder": ""}
            for index, image_id in enumerate(validation.reference_image_ids)
        ]
        data["global"]["commonEnabled"] = True
        data["global"]["commonCollapsed"] = True
        data["timelineMode"] = H3_REFERENCE_TIMELINE_MODE
        for segment in data["segments"]:
            segment["taskType"] = 'fl2v' if segment.get('endImage') else 'i2v' if segment.get('genImage') else 'r2v'
        timeline_data = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    semantic_values = _director_semantic_values(timeline_data)
    if validation.route == "h3_ref":
        for key in ("refine_width", "refine_height", "refine_aspect_ratio", "refine_megapixels"):
            semantic_values.pop(key, None)
    return DirectorPayload(validation.route, timeline_data, semantic_values)


def compile_director_preview(
    draft: DirectorDraft,
    optimized: OptimizedDirector,
    *,
    reference_limit: int = 5,
) -> DirectorPayload:
    """Compile deterministic H3 payload with stable image-ID placeholders."""
    validation = validate_generation(draft, reference_limit=reference_limit)
    frame_ids = [frame.image_id for segment in draft.segments
                 for frame in (segment.first_frame, segment.last_frame) if frame is not None]
    upload_ids = dict.fromkeys((*validation.reference_image_ids, *frame_ids))
    placeholders = {
        image_id: {"imageFile": f"preview-image:{image_id}"}
        for image_id in upload_ids
    }
    return compile_director_payload(
        draft, optimized, placeholders, reference_limit=reference_limit,
    )


__all__ = ["DirectorPayload", "compile_director_payload", "compile_director_preview"]
