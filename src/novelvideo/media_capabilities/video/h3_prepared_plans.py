"""Revision-pinned external H3 director plans for narrative-group generation."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

from .h3_director_plan import H3DirectorPlan


_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True)
EMPTY_VIDEO_KEYFRAME_BINDING_SHA256 = hashlib.sha256(
    b"novelvideo:empty-video-keyframe-binding:v1"
).hexdigest()
EMPTY_H3_REFERENCE_BINDING_SHA256 = hashlib.sha256(
    b'{"provider_workflow_id":"","references":[],"schema_version":1}'
).hexdigest()
EMPTY_H3_SOURCE_REUSE_DIGEST = hashlib.sha256(
    b"novelvideo:empty-h3-source-reuse:v1"
).hexdigest()


class H3PreparedPlanPins(BaseModel):
    model_config = _MODEL_CONFIG

    project_id: str = Field(min_length=1)
    episode: int = Field(gt=0)
    group_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    requested_mode: str = Field(min_length=1)
    dialogue_source: Literal["external_tts", "h3_native"] | None = None
    video_revision: int = Field(ge=0)
    plan_revision: int = Field(ge=1)
    settings_revision: int = Field(ge=0)
    reference_revision: int = Field(ge=0)
    reference_binding_sha256: str = Field(
        default=EMPTY_H3_REFERENCE_BINDING_SHA256,
        pattern=r"^[0-9a-f]{64}$",
    )
    source_reuse_digest: str = Field(
        default=EMPTY_H3_SOURCE_REUSE_DIGEST,
        pattern=r"^[0-9a-f]{64}$",
    )
    director_revision_id: str = Field(min_length=1)
    keyframe_revision: int = Field(ge=0)
    keyframe_binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class H3PreparedPlanSegment(BaseModel):
    model_config = _MODEL_CONFIG

    segment_id: str = Field(min_length=1)
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    director_plan: H3DirectorPlan


class H3PreparedPlanImport(BaseModel):
    """Public import envelope; every segment is bound to an exact source hash."""

    model_config = _MODEL_CONFIG

    schema_version: Literal[1] = 1
    preparation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pins: H3PreparedPlanPins
    segments: tuple[H3PreparedPlanSegment, ...] = Field(min_length=1)


def prepared_h3_context_digest(
    pins: H3PreparedPlanPins | Mapping[str, Any],
    segment_hashes: list[tuple[str, str]] | tuple[tuple[str, str], ...],
) -> str:
    """Hash the revision pins and ordered compiler inputs as a single CAS key."""

    pin_payload = (
        pins.model_dump(mode="json") if isinstance(pins, BaseModel) else dict(pins)
    )
    # Keep no-reference H3 plans byte-compatible with the original public
    # digest shape. EXTERNAL reference and source-reuse bindings are included
    # only when they carry a non-empty snapshot hash.
    if pin_payload.get("reference_binding_sha256") == EMPTY_H3_REFERENCE_BINDING_SHA256:
        pin_payload.pop("reference_binding_sha256", None)
    if pin_payload.get("source_reuse_digest") == EMPTY_H3_SOURCE_REUSE_DIGEST:
        pin_payload.pop("source_reuse_digest", None)
    payload = {
        "schema_version": 1,
        "pins": pin_payload,
        "segments": [
            {"segment_id": segment_id, "input_hash": input_hash}
            for segment_id, input_hash in segment_hashes
        ],
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def prepared_h3_reference_binding_digest(
    references, *, provider_workflow_id: str = ""
) -> str:
    """Hash ordered reference identities and the bytes selected for H3-Ref."""

    if not isinstance(provider_workflow_id, str):
        raise TypeError("provider_workflow_id must be a string")
    records = []
    for index, reference in enumerate(tuple(references), start=1):
        content = getattr(reference, "content", None)
        if not isinstance(content, bytes):
            raise TypeError("prepared H3 references must contain resolved bytes")
        content_sha256 = hashlib.sha256(content).hexdigest()
        if content_sha256 != getattr(reference, "sha256", None):
            raise ValueError("prepared H3 reference sha256 does not match its bytes")
        record = {
            "index": index,
            "reference_id": getattr(reference, "reference_id", None),
            "source_kind": str(getattr(reference, "source_kind", "")),
            "label": getattr(reference, "label", None),
            "subject_description": getattr(reference, "subject_description", None),
            "sha256": content_sha256,
            "byte_length": len(content),
            "asset_id": str(getattr(reference, "asset_id", "") or ""),
            "temporary_upload_id": str(
                getattr(reference, "temporary_upload_id", "") or ""
            ),
        }
        if any(
            not isinstance(record[name], str) or not record[name].strip()
            for name in ("reference_id", "source_kind", "label", "subject_description")
        ):
            raise ValueError("prepared H3 reference identity metadata is incomplete")
        records.append(record)
    encoded = json.dumps(
        {
            "schema_version": 1,
            "provider_workflow_id": provider_workflow_id.strip(),
            "references": records,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def prepared_h3_plan_digest(plan: H3DirectorPlan | Mapping[str, Any]) -> str:
    payload = (
        plan.model_dump(mode="json") if isinstance(plan, BaseModel) else dict(plan)
    )
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "EMPTY_H3_REFERENCE_BINDING_SHA256",
    "EMPTY_H3_SOURCE_REUSE_DIGEST",
    "EMPTY_VIDEO_KEYFRAME_BINDING_SHA256",
    "H3PreparedPlanImport",
    "H3PreparedPlanPins",
    "H3PreparedPlanSegment",
    "prepared_h3_context_digest",
    "prepared_h3_plan_digest",
    "prepared_h3_reference_binding_digest",
]
