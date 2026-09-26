"""Pure, fail-closed structured H3 prompt optimization for canvas Director."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Mapping

from pydantic import BaseModel, ConfigDict

from novelvideo.knowledge_runtime.codex import StructuredImage, validate_structured_images
from novelvideo.media_capabilities.video.h3_prompt_profile import (
    H3_CANVAS_WRITING_RULES, H3_PROMPT_PROFILE_ID, H3_PROMPT_PROFILE_VERSION,
)
from novelvideo.media_capabilities.video.h3_wire import compile_h3_wire
from novelvideo.text_task_runtime.runtime import StructuredTextRuntime

from .capabilities import validate_generation
from .models import (
    CanvasBaseWire, CanvasReferenceWire, DirectorDraft, OptimizedDirector,
    OptimizedSegment,
)


class _OptimizedWireResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    segment_id: str
    wire: CanvasBaseWire | CanvasReferenceWire


def _reference_facts(draft: DirectorDraft) -> list[dict[str, object]]:
    seen: set[str] = set()
    subjects: dict[str, int] = {}
    facts: list[dict[str, object]] = []
    for reference in draft.references:
        if reference.image_id in seen:
            continue
        seen.add(reference.image_id)
        subject_key = reference.character_id or f"image:{reference.image_id}"
        subject_number = subjects.setdefault(subject_key, len(subjects) + 1)
        facts.append({
            "picture_tag": f"<Picture {len(facts) + 1}>",
            "subject_tag": f"<Subject {subject_number}>",
            "image_id": reference.image_id,
            "asset_id": reference.asset_id,
            "character_id": reference.character_id,
            "variant_id": reference.variant_id,
            "variant_label": reference.variant_label,
            "asset_kind": reference.asset_kind,
        })
    return facts


async def optimize(
    runtime: StructuredTextRuntime,
    draft: DirectorDraft,
    *,
    frozen_images: Mapping[str, StructuredImage],
    system_prompt: str | None = None,
    reference_limit: int = 5,
) -> OptimizedDirector:
    """Optimize each ordered segment using frozen trusted image bytes.

    Runtime errors and invalid structured output propagate; raw editor prose is
    never treated as a provider prompt.
    """
    validation = validate_generation(draft, reference_limit=reference_limit)
    reference_facts = _reference_facts(draft)
    reference_ids = [str(fact["image_id"]) for fact in reference_facts]
    result: list[OptimizedSegment] = []
    for source, aligned in zip(draft.segments, validation.timeline, strict=True):
        image_ids = (reference_ids if validation.route == "h3_ref" else
                     [source.first_frame.image_id] +
                     ([source.last_frame.image_id] if source.last_frame else []))
        try:
            images = [frozen_images[image_id] for image_id in image_ids]
        except KeyError as exc:
            raise ValueError(f"frozen image is missing: {exc.args[0]}") from exc
        validate_structured_images(images)
        mode = {"i2v": "i2va", "fl2v": "fl2va", "ref_only": "ref2va"}[aligned.mode]
        source_data = {
            "segment_id": source.id,
            "mode": mode,
            "source_prompt": source.prompt,
            "requested_duration_seconds": source.duration_seconds,
            "duration_seconds": aligned.duration_seconds,
            "frames": aligned.frames,
            "aspect_ratio": draft.aspect_ratio,
            "resolution": draft.resolution,
            "references": reference_facts if reference_ids else [],
            "frame_images": [] if reference_ids else [
                {"picture_tag": f"<Picture {index + 1}>", "image_id": image_id,
                 "anchor": "first" if index == 0 else "last"}
                for index, image_id in enumerate(image_ids)
            ],
        }
        prompt = ("Return a structured H3 wire for this one segment. Copy segment_id exactly. "
                  "Use the images in the exact order listed. Preserve quoted dialogue verbatim.\n"
                  "INPUT_JSON:\n" + json.dumps(source_data, ensure_ascii=False))
        response = await runtime.run_structured(
            prompt=prompt, output_type=_OptimizedWireResponse,
            system_prompt=system_prompt or H3_CANVAS_WRITING_RULES, images=images,
        )
        response = _OptimizedWireResponse.model_validate(response)
        if response.segment_id != source.id:
            raise ValueError(f"optimized segment ID mismatch: {source.id}")
        wire = response.wire
        if wire.mode != mode or (isinstance(wire, CanvasReferenceWire) != (mode == "ref2va")):
            raise ValueError(f"optimized segment mode mismatch: {source.id}")
        if abs(wire.duration_seconds - aligned.duration_seconds) > 1e-9:
            raise ValueError(f"optimized segment duration mismatch: {source.id}")
        result.append(OptimizedSegment(
            segment_id=source.id, mode=mode,
            requested_duration_seconds=source.duration_seconds,
            duration_seconds=aligned.duration_seconds, frames=aligned.frames,
            wire=wire, prompt=compile_h3_wire(wire),
        ))
    return OptimizedDirector(
        revision=draft.revision, route=validation.route,
        profile_id=H3_PROMPT_PROFILE_ID, profile_version=H3_PROMPT_PROFILE_VERSION,
        optimized_at=datetime.now(timezone.utc), segments=tuple(result),
    )


__all__ = ["optimize"]
