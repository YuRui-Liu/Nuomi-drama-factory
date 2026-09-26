"""Pure generation checks for the implemented MiniMax H3 adapters.

The reference limit is caller supplied (configured workflow default: 5). It
counts distinct reference image IDs; first/last frames are separate inputs.
"""

from __future__ import annotations

from dataclasses import dataclass

from novelvideo.media_capabilities.video.h3_size_settings import (
    H3SizeSetting,
    H3SizeSettingError,
    resolve_h3_size_setting,
)
from novelvideo.media_capabilities.video.h3_timeline import H3_FPS, frames_for_duration

from .models import DirectorDraft


class DirectorCapabilityError(ValueError):
    """A generation error addressable by an editor field and segment ID."""

    def __init__(self, field: str, message: str, segment_id: str | None = None):
        self.field = field
        self.segment_id = segment_id
        super().__init__(f"{field}: {message}")


@dataclass(frozen=True, slots=True)
class AlignedSegment:
    id: str
    mode: str
    requested_duration_seconds: float
    frames: int
    start_frame: int
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class GenerationValidation:
    route: str
    modes: tuple[str, ...]
    size: H3SizeSetting
    reference_image_ids: tuple[str, ...]
    timeline: tuple[AlignedSegment, ...]
    total_frames: int
    actual_duration_seconds: float

    @property
    def reference_count(self) -> int:
        return len(self.reference_image_ids)


def resolve_route(reference_count: int) -> str:
    """Use the reference adapter exactly when global references are present."""
    if isinstance(reference_count, bool) or not isinstance(reference_count, int) or reference_count < 0:
        raise ValueError("reference_count must be a non-negative integer")
    return "h3_ref" if reference_count else "h3"


def _validate_reference_limit(reference_limit: int) -> None:
    if isinstance(reference_limit, bool) or not isinstance(reference_limit, int) or reference_limit < 1:
        raise ValueError("reference_limit must be a positive integer")


def validate_generation(draft: DirectorDraft, reference_limit: int = 5) -> GenerationValidation:
    """Validate one immutable draft and return its route and aligned timeline.

    Raises ``DirectorCapabilityError`` with ``field`` and optional
    ``segment_id``. The draft itself is never changed.
    """
    _validate_reference_limit(reference_limit)
    if draft.model_id != "minimax-h3":
        raise DirectorCapabilityError("model_id", f"unsupported model {draft.model_id!r}")
    try:
        size = resolve_h3_size_setting(draft.resolution, draft.aspect_ratio)
    except H3SizeSettingError as exc:
        field = "resolution" if "resolution" in str(exc) else "aspect_ratio"
        raise DirectorCapabilityError(field, str(exc)) from exc

    reference_ids = tuple(dict.fromkeys(image.image_id for image in draft.references))
    if len(reference_ids) > reference_limit:
        raise DirectorCapabilityError("references", f"at most {reference_limit} distinct reference images are configured")
    if not draft.segments:
        raise DirectorCapabilityError("segments", "at least one segment is required")

    route = resolve_route(len(reference_ids))
    timeline: list[AlignedSegment] = []
    offset = 0
    for index, segment in enumerate(draft.segments):
        field = f"segments[{index}]"
        if not segment.prompt.strip():
            raise DirectorCapabilityError(f"{field}.prompt", "prompt is required", segment.id)
        if reference_ids:
            if segment.first_frame is not None:
                raise DirectorCapabilityError(f"{field}.first_frame", "reference images with a first frame are unsupported", segment.id)
            if segment.last_frame is not None:
                raise DirectorCapabilityError(f"{field}.last_frame", "reference images with a last frame are unsupported", segment.id)
            mode = "ref_only"
        else:
            if segment.first_frame is None:
                raise DirectorCapabilityError(f"{field}.first_frame", "first frame is required without references", segment.id)
            mode = "fl2v" if segment.last_frame is not None else "i2v"
        try:
            frames = frames_for_duration(segment.duration_seconds, H3_FPS)
            actual_duration = frames / H3_FPS
            total_duration = (offset + frames) / H3_FPS
        except OverflowError as exc:
            raise DirectorCapabilityError(
                f"{field}.duration_seconds",
                "duration overflows the H3 frame timeline",
                segment.id,
            ) from exc
        timeline.append(AlignedSegment(segment.id, mode, segment.duration_seconds,
                                       frames, offset, actual_duration))
        offset += frames
    return GenerationValidation(route, tuple(item.mode for item in timeline), size,
                                reference_ids, tuple(timeline), offset, total_duration)


def describe_capabilities(reference_limit: int = 5) -> dict:
    """Public product capabilities; no unverified provider maxima are claimed."""
    _validate_reference_limit(reference_limit)
    sizes = tuple(resolve_h3_size_setting(resolution, ratio) for resolution in ("720p", "1080p")
                  for ratio in ("9:16", "16:9"))
    return {
        "models": ({"id": "minimax-h3", "label": "MiniMax H3", "adapter": "h3",
                    "reference_adapter": "h3_ref"},),
        "reference_limit": reference_limit,
        "fps": H3_FPS,
        "frame_rule": "smallest 17k+5 frames at or above requested duration",
        "params": {"resolution": ("720p", "1080p"),
                   "aspect_ratio": ("9:16", "16:9")},
        "sizes": tuple({"resolution": item.resolution, "aspect_ratio": item.aspect_ratio,
                        "width": item.width, "height": item.height,
                        "megapixels": item.megapixels} for item in sizes),
        "modes": (
            {"id": "i2v", "supported": True},
            {"id": "fl2v", "supported": True},
            {"id": "ref_only", "supported": True},
            {"id": "ref_plus_first", "supported": False},
            {"id": "ref_plus_first_last", "supported": False},
            {"id": "text_only", "supported": False},
            {"id": "last_only", "supported": False},
        ),
    }


__all__ = ["DirectorCapabilityError", "AlignedSegment", "GenerationValidation",
           "resolve_route", "validate_generation", "describe_capabilities"]
