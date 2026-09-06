"""Narrative-group aggregate API.

The sidecar is the server-side source of truth. Clients never submit their own
cell mapping, which prevents refreshes and retries from silently reshuffling a
grid.
"""

from __future__ import annotations

import math
import re
import uuid
from io import BytesIO
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal, Mapping
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile, status
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import (
    get_media_capability_store,
    get_media_credential_resolver,
    make_sqlite_store_for_context,
    resolve_project_scope,
)
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.media_capabilities.models import GRSAI_IMAGE_MODELS
from novelvideo.media_capabilities.runtime.credentials import CredentialResolver
from novelvideo.media_capabilities.store import MediaCapabilityStore
from novelvideo.media_capabilities.video.parameters import (
    VideoWorkflowParameterError,
    resolve_workflow_parameters,
)
from novelvideo.media_capabilities.video.workflow_registry import (
    VideoWorkflowScene,
    VideoWorkflowUnavailable,
    build_video_workflow_registry,
)
from novelvideo.narrative_groups.models import NarrativeGroup, StageName
from novelvideo.narrative_groups.references import (
    MAX_GROUP_IMAGE_REFERENCES,
    GroupReferencePreview,
    UnknownGroupReferenceIds,
    apply_group_reference_selection,
    resolve_group_reference_preview,
)
from novelvideo.narrative_groups.service import (
    advance_revision,
    generation_beats_for_group,
    load_effective_groups,
    load_group_video_prompt_manifest,
    rebuild_groups,
    rollback_stage_revision,
    reserve_video_revision,
    restore_video_reservation,
    stage_history,
    update_video_manifest_dialogue_source,
    update_video_plan,
    update_video_reference_settings,
    update_video_settings,
)
from novelvideo.narrative_groups.video_references import (
    MAX_VIDEO_REFERENCE_BYTES,
    MAX_VIDEO_REFERENCE_PIXELS,
    VideoReferenceCandidate,
    VideoReferencePreview,
    VideoReferenceSelection,
    delete_temporary_video_reference,
    resolve_group_video_reference_preview,
    resolve_saved_video_references,
    temporary_upload_path,
    write_temporary_video_reference,
)
from novelvideo.ports import get_task_backend
from novelvideo.utils.path_resolver import (
    canonical_identity_path,
    canonical_portrait_path,
    canonical_prop_reference_path,
    canonical_scene_master_path,
)
from novelvideo.utils.upload_safety import MAX_UPLOAD_BYTES

router = APIRouter()


class NarrativeGroupGenerationRequest(BaseModel):
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
    use_style: bool = True
    selected_character_reference_ids: list[str] | None = None
    selected_scene_reference_ids: list[str] | None = None
    provider_id: str | None = None
    model: str | None = None
    image_size: Literal["1K", "2K", "4K"] | None = None
    allow_unconstrained: bool = False


def _image_binding(
    ctx, project_dir: Path, stage: StageName, request: NarrativeGroupGenerationRequest
) -> tuple[str, str, str]:
    from novelvideo.project_config import load_project_config_from_state_dir

    config = load_project_config_from_state_dir(
        getattr(ctx, "state_dir", project_dir),
        username=getattr(ctx, "owner_username", ""),
        project=getattr(ctx, "project_name", ""),
    )
    prefix = "narrative_sketch" if stage == "sketch" else "narrative_render"
    provider_id = str(
        request.provider_id or config.get(f"{prefix}_provider") or "grsai-main"
    ).strip()
    default_model = "nano-banana-2" if stage == "sketch" else "gpt-image-2"
    model = str(request.model or config.get(f"{prefix}_model") or default_model).strip()
    account = get_media_capability_store().get_provider(provider_id)
    if account is None or account.provider_type != "grsai" or not account.enabled:
        raise HTTPException(
            status_code=422, detail="Selected GRSAI image provider is unavailable"
        )
    if model not in GRSAI_IMAGE_MODELS:
        raise HTTPException(status_code=422, detail="Unsupported GRSAI image model")
    image_size = "1K"
    if stage == "render":
        from novelvideo.narrative_groups.image_resolution import (
            supported_grid_image_sizes,
        )

        image_size = str(
            request.image_size
            or config.get("narrative_render_image_size")
            or ("2K" if model == "gpt-image-2-vip" else "1K")
        ).strip()
        if image_size not in supported_grid_image_sizes(model):
            raise HTTPException(
                status_code=422,
                detail="Image size is unsupported by the selected narrative render model",
            )
    return provider_id, model, image_size


class NarrativeGroupVideoRequest(BaseModel):
    """Client input deliberately excludes mutable frame and beat payloads."""

    model_config = ConfigDict(extra="forbid")
    model: str = Field(default="runninghub:minimax-h3", min_length=1)
    mode: Literal["auto", "i2va", "fl2va"] = "auto"
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
    resolution: str | None = None
    revision: int = Field(ge=0)
    plan_revision: int = Field(ge=1)
    settings_revision: int | None = Field(default=None, ge=0)
    reference_revision: int | None = Field(default=None, ge=0)


class VideoReferenceSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference_id: str = Field(min_length=1)
    subject_description: str = Field(min_length=1)

    @field_validator("reference_id", mode="before")
    @classmethod
    def normalize_reference_id(cls, value: str) -> str:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be blank")
        return normalized

    @field_validator("subject_description", mode="before")
    @classmethod
    def normalize_subject_description(cls, value: str) -> str:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be blank")
        if "\n" in normalized or "\r" in normalized:
            raise ValueError("value must be a single line")
        if len(normalized) > 500:
            raise ValueError("value exceeds the 500 character limit")
        return normalized


class UpdateVideoReferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    references: list[VideoReferenceSelectionRequest]


class NarrativeGroupVideoSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    workflow_id: str = Field(min_length=1)
    overrides: dict[str, str] = Field(default_factory=dict)


class NarrativeGroupVideoPlanUnitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    beat_ids: list[str] = Field(min_length=1, max_length=2)


class NarrativeGroupVideoPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    units: list[NarrativeGroupVideoPlanUnitRequest] = Field(min_length=1)


class NarrativeGroupDialogueSourceRequest(BaseModel):
    """A composition preference for one logical span of a director output."""

    model_config = ConfigDict(extra="forbid")
    span_index: int = Field(ge=0)
    dialogue_source: Literal["external_tts", "h3_native"]
    revision: int = Field(ge=1)


class NarrativeGroupStyleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    style_id: str | None = Field(default=None, min_length=1)
    action: Literal["restyle", "redirect"] = "restyle"


async def _resolve_groups(project: str, episode: int, user: dict, *, rebuild: bool = False):
    resolved = await resolve_project_scope(project, user, required_role="editor")
    store = await make_sqlite_store_for_context(resolved.ctx)
    beats = await store.get_beats_as_dicts(episode)
    if rebuild:
        if DirectorPlanStore(resolved.project_dir).load_active(episode) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "DIRECTOR_PLAN_ACTIVE",
                    "message": "Active director plan controls narrative groups",
                },
            )
        groups = rebuild_groups(resolved.project_dir, episode, beats)
    else:
        groups = load_effective_groups(resolved.project_dir, episode, beats)
    return resolved, groups, beats


def _project_video_workflow_defaults(resolved, workflow) -> dict[str, str]:
    from novelvideo.project_config import load_project_config_from_state_dir

    config = load_project_config_from_state_dir(
        getattr(resolved.ctx, "state_dir", resolved.project_dir),
        username=getattr(resolved.ctx, "owner_username", ""),
        project=getattr(resolved.ctx, "project_name", ""),
    )
    namespaces = config.get("video_workflow_parameters")
    raw = namespaces.get(workflow.id) if isinstance(namespaces, Mapping) else None
    overrides = dict(raw) if isinstance(raw, Mapping) else {}
    if (
        workflow.id == "runninghub:minimax-h3"
        and not overrides
        and config.get("video_resolution") in {"720p", "1080p"}
    ):
        overrides["resolution"] = config["video_resolution"]
    return resolve_workflow_parameters(workflow, overrides)


def _asset_url(project: str, project_dir: Path, value: str) -> str:
    if not value:
        return ""
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = project_dir / candidate
    try:
        relative = candidate.resolve().relative_to(project_dir.resolve())
    except ValueError:
        return ""
    encoded_project = quote(project, safe="")
    encoded_path = quote(relative.as_posix(), safe="/")
    return f"/api/v1/projects/{encoded_project}/media/{encoded_path}"


def _video_reference_max_images(media_store: MediaCapabilityStore) -> int:
    return int(
        media_store.get_runninghub_workflows().video_minimax_h3_ref_max_images
    )


def _video_reference_candidate_path(
    project_dir: Path,
    episode: int,
    group_id: str,
    candidate: VideoReferenceCandidate,
) -> Path:
    if candidate.source_kind == "temporary_upload":
        return temporary_upload_path(
            project_dir, episode, group_id, candidate.temporary_upload_id
        )
    if candidate.source_kind == "scene_master":
        return canonical_scene_master_path(project_dir, candidate.asset_id)
    if candidate.source_kind == "prop_reference":
        return canonical_prop_reference_path(project_dir, candidate.asset_id)
    if candidate.source_kind == "character_identity":
        identity = canonical_identity_path(
            project_dir, candidate.character_name, candidate.asset_id
        )
        return (
            identity
            if identity.is_file()
            else canonical_portrait_path(project_dir, candidate.character_name)
        )
    raise ValueError("unsupported video reference source kind")


def _serialize_video_reference_candidate(
    project: str,
    project_dir: Path,
    episode: int,
    group_id: str,
    candidate: VideoReferenceCandidate,
) -> dict[str, str]:
    path = _video_reference_candidate_path(
        project_dir, episode, group_id, candidate
    )
    thumbnail_url = _asset_url(project, project_dir, str(path))
    if not thumbnail_url:
        raise ValueError("video reference thumbnail path is unsafe")
    return {
        "reference_id": candidate.reference_id,
        "source_kind": candidate.source_kind,
        "label": candidate.label,
        "subject_description": candidate.subject_description,
        "thumbnail_url": thumbnail_url,
    }


def _serialize_video_reference_preview(
    project: str,
    project_dir: Path,
    episode: int,
    group_id: str,
    preview: VideoReferencePreview,
) -> dict[str, Any]:
    return {
        "revision": preview.revision,
        "max_images": preview.max_images,
        "candidates": [
            _serialize_video_reference_candidate(
                project, project_dir, episode, group_id, candidate
            )
            for candidate in preview.candidates
        ],
        "selected": [
            {
                "reference_id": reference.reference_id,
                "subject_description": reference.subject_description,
            }
            for reference in preview.references
        ],
        "warnings": list(preview.warnings),
    }


def _normalize_video_reference_upload(content: bytes, content_type: str) -> bytes:
    accepted = {
        "image/jpeg": "JPEG",
        "image/png": "PNG",
        "image/webp": "WEBP",
    }
    expected_format = accepted.get(content_type.lower())
    if expected_format is None:
        raise ValueError("video reference must be JPEG, PNG, or WebP")
    if not content:
        raise ValueError("video reference upload cannot be empty")
    try:
        with Image.open(BytesIO(content)) as image:
            if image.format != expected_format:
                raise ValueError("video reference MIME type does not match its image")
            if image.width * image.height > MAX_VIDEO_REFERENCE_PIXELS:
                raise ValueError("video reference exceeds the 40 megapixel limit")
            image.load()
            normalized = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ValueError("video reference image cannot be decoded") from exc
    output = BytesIO()
    normalized.save(output, format="PNG")
    return output.getvalue()


def _serialize(project: str, project_dir: Path, groups: list[NarrativeGroup]) -> list[dict]:
    result = []
    for group in groups:
        item = group.to_dict()
        effective_style = dict(item.get("effective_style_snapshot") or {})
        snapshot_id = str(effective_style.get("snapshot_id") or "project-default")
        effective_style.update({
            "snapshot_id": snapshot_id,
            "style_id": str(effective_style.get("style_id") or snapshot_id),
            "style_version": str(effective_style.get("style_version") or "1"),
            "catalog_hash": str(effective_style.get("catalog_hash") or snapshot_id),
            "style_hash": str(effective_style.get("style_hash") or snapshot_id),
            "inherited": effective_style.get("source", "inherited") == "inherited",
        })
        item["effective_style_snapshot"] = effective_style
        for stage_name, state in item["stages"].items():
            state.pop("revision_history", None)
            if stage_name == "video":
                manifest_name = str(state.get("manifest_asset") or "").strip()
                if manifest_name:
                    try:
                        from novelvideo.media_capabilities.video.h3_timeline import load_h3_director_manifest

                        manifest = load_h3_director_manifest(manifest_name)
                        state["video_spans"] = [
                            {
                                "span_index": index,
                                "beat_numbers": [entry.segment.beat_number],
                                "start_seconds": entry.start_seconds,
                                "end_seconds": entry.end_seconds,
                                "dialogue_source": entry.dialogue_source.value,
                            }
                            for index, entry in enumerate(manifest.entries)
                        ]
                        if not state.get("video_asset"):
                            state["video_asset"] = manifest.physical_video
                        if not state.get("original_audio_path"):
                            state["original_audio_path"] = manifest.original_audio_path or ""
                        if not state.get("dialogue_stem_path"):
                            state["dialogue_stem_path"] = manifest.dialogue_stem_path or ""
                        if not state.get("ambience_stem_path"):
                            state["ambience_stem_path"] = manifest.ambience_stem_path or ""
                    except (OSError, ValueError):
                        state["video_spans"] = []
                else:
                    state["video_spans"] = []
            state["grid_asset"] = _asset_url(
                project, project_dir, state.get("grid_asset", "")
            )
            for cell in state.get("cell_assets") or []:
                url = _asset_url(project, project_dir, str(cell.get("path") or ""))
                cell["path"] = url
                cell["url"] = url
            for field in (
                "video_asset", "manifest_asset", "original_audio_path",
                "dialogue_stem_path", "ambience_stem_path",
            ):
                state[field] = _asset_url(project, project_dir, state.get(field, ""))
        result.append(item)
    return result


def _group_beats(
    groups: list[NarrativeGroup], beats: list[dict], group_id: str
) -> tuple[NarrativeGroup, list[dict]]:
    group = next((item for item in groups if item.id == group_id), None)
    if group is None:
        raise KeyError(group_id)
    beat_by_id = {
        str(beat.get("id") or beat.get("beat_id") or beat.get("beat_number")): beat
        for beat in beats
    }
    return group, [beat_by_id[beat_id] for beat_id in group.beat_ids if beat_id in beat_by_id]


def _serialize_reference_preview(
    project: str, project_dir: Path, preview: GroupReferencePreview
) -> dict:
    selection = apply_group_reference_selection(preview)
    selected_ids = {reference.id for reference in selection.selected}
    omitted_ids = [reference.id for reference in selection.omitted]

    def image_item(reference) -> dict:
        return {
            "id": reference.id,
            "kind": reference.kind,
            "source_kind": reference.source_kind,
            "label": reference.label,
            "thumbnail_url": _asset_url(project, project_dir, reference.path),
            "beat_numbers": list(reference.beat_numbers),
            "enabled_by_default": reference.id in selected_ids,
            "character_name": reference.character_name or None,
            "identity_id": reference.identity_id or None,
            "scene_id": reference.scene_id or None,
            "warning": reference.warning,
        }

    return {
        "style": {
            "id": preview.style.id,
            "label": preview.style.name,
            "prompt": preview.style.prompt,
            "enabled_by_default": True,
            "warning": preview.style.warning,
        },
        "character_references": [
            image_item(reference)
            for reference in preview.image_references
            if reference.kind == "character"
        ],
        "scene_references": [
            image_item(reference)
            for reference in preview.image_references
            if reference.kind == "scene"
        ],
        "limits": {
            "max_images": MAX_GROUP_IMAGE_REFERENCES,
            "selected_images": len(selection.selected),
            "omitted_reference_ids": omitted_ids,
        },
        "warnings": list(selection.warnings),
    }


_REJECTED_REVIEW_VALUE = object()
_SAFE_TEXT = object()
_SAFE_INT = object()
_SAFE_NUMBER = object()
_SAFE_BOOL = object()
_MAX_REVIEW_ID_LENGTH = 256
_MAX_REVIEW_TEXT_LENGTH = 16 * 1024
_MAX_REVIEW_PROMPT_LENGTH = 256 * 1024
_MAX_REVIEW_BEAT_IDS = 32
_URI_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_CAMERA_PLAN_SCHEMA = {
    "type": _SAFE_TEXT,
    "direction": _SAFE_TEXT,
    "amplitude": _SAFE_TEXT,
    "speed": _SAFE_TEXT,
}
_ACTION_PLAN_SCHEMA = {
    "phase": _SAFE_TEXT,
    "start_frame": _SAFE_INT,
    "end_frame": _SAFE_INT,
    "description": _SAFE_TEXT,
}
_DIALOGUE_CUE_SCHEMA = {
    "start_frame": _SAFE_INT,
    "end_frame": _SAFE_INT,
    "speaker": _SAFE_TEXT,
    "speaker_id": _SAFE_TEXT,
    "text": _SAFE_TEXT,
    "language": _SAFE_TEXT,
    "continuation": _SAFE_BOOL,
    "truncated": _SAFE_BOOL,
}
_SHOT_PLAN_SCHEMA = {
    "shot_id": _SAFE_TEXT,
    "start_frame": _SAFE_INT,
    "end_frame": _SAFE_INT,
    "framing": _SAFE_TEXT,
    "angle": _SAFE_TEXT,
    "focus": _SAFE_TEXT,
    "composition": _SAFE_TEXT,
    "camera": _CAMERA_PLAN_SCHEMA,
    "actions": [_ACTION_PLAN_SCHEMA],
    "dialogue": [_DIALOGUE_CUE_SCHEMA],
}
_DIRECTOR_PLAN_SCHEMA = {
    "mode": _SAFE_TEXT,
    "fps": _SAFE_INT,
    "total_frames": _SAFE_INT,
    "visual_style": _SAFE_TEXT,
    "continuity_locks": [_SAFE_TEXT],
    "shots": [_SHOT_PLAN_SCHEMA],
    "frame_differences": [{
        "description": _SAFE_TEXT,
        "convergence_frame": _SAFE_INT,
    }],
    "soundscape": _SAFE_TEXT,
    "music": _SAFE_TEXT,
}
_PROMPT_PROFILE_SCHEMA = {
    "id": _SAFE_TEXT,
    "version": _SAFE_INT,
    "compiler_version": _SAFE_INT,
}
_QUALITY_REPORT_SCHEMA = {
    "passed": _SAFE_BOOL,
    "issues": [{
        "code": _SAFE_TEXT,
        "message": _SAFE_TEXT,
        "severity": _SAFE_TEXT,
        "location": _SAFE_TEXT,
    }],
    "version": _SAFE_INT,
}
_INPUT_SUMMARY_SCHEMA = {
    "beat_ids": [_SAFE_TEXT],
    "mode": _SAFE_TEXT,
    "duration_seconds": _SAFE_NUMBER,
    "aspect_ratio": _SAFE_TEXT,
    "resolution": _SAFE_TEXT,
    "first_frame_sha256": _SAFE_TEXT,
    "last_frame_sha256": _SAFE_TEXT,
}


def _manifest_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    return dict(dump(mode="json")) if callable(dump) else {}


def _project_review_value(value: Any, schema: Any) -> Any:
    if schema is _SAFE_TEXT:
        if (
            isinstance(value, str)
            and len(value) <= _MAX_REVIEW_TEXT_LENGTH
            and not _is_path_or_uri(value)
        ):
            return value
        return _REJECTED_REVIEW_VALUE
    if schema is _SAFE_INT:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return _REJECTED_REVIEW_VALUE
    if schema is _SAFE_NUMBER:
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        ):
            return value
        return _REJECTED_REVIEW_VALUE
    if schema is _SAFE_BOOL:
        if isinstance(value, bool):
            return value
        return _REJECTED_REVIEW_VALUE
    if isinstance(schema, dict):
        source = _manifest_mapping(value)
        projected = {}
        for key, child_schema in schema.items():
            if key not in source:
                continue
            child = _project_review_value(source[key], child_schema)
            if child is not _REJECTED_REVIEW_VALUE:
                projected[key] = child
        return projected
    if isinstance(schema, list) and len(schema) == 1:
        if not isinstance(value, (list, tuple)):
            return []
        return [
            child
            for item in value
            if (child := _project_review_value(item, schema[0]))
            is not _REJECTED_REVIEW_VALUE
        ]
    return _REJECTED_REVIEW_VALUE


def _is_path_or_uri(value: str) -> bool:
    stripped = value.strip()
    return bool(
        _URI_RE.match(stripped)
        or stripped.casefold().startswith("file://")
        or stripped.startswith(("\\\\", "//"))
        or PureWindowsPath(stripped).drive
        or PureWindowsPath(stripped).is_absolute()
        or PurePosixPath(stripped).is_absolute()
    )


def _safe_review_string(value: Any, *, max_length: int = _MAX_REVIEW_ID_LENGTH) -> str:
    if (
        isinstance(value, str)
        and len(value) <= max_length
        and not _is_path_or_uri(value)
    ):
        return value
    return ""


def _first_safe_review_string(*values: Any) -> str:
    for value in values:
        safe = _safe_review_string(value)
        if safe:
            return safe
    return ""


def _safe_prompt(value: Any) -> str:
    return value if isinstance(value, str) and len(value) <= _MAX_REVIEW_PROMPT_LENGTH else ""


def _safe_duration(*values: Any) -> float | int:
    for value in values:
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and 0 <= value <= 3600
        ):
            return value
    return 0


def _safe_frame_reference(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 2048 or _URI_RE.match(value.strip()):
        return ""
    return value


def _review_beat_ids(entry: Mapping[str, Any], segment: Mapping[str, Any]) -> list[str]:
    summary = _project_review_value(
        entry.get("input_summary"), _INPUT_SUMMARY_SCHEMA
    )
    beat_ids = [
        safe
        for value in (summary.get("beat_ids") or ())[:_MAX_REVIEW_BEAT_IDS]
        if (safe := _safe_review_string(value))
    ]
    if beat_ids:
        return beat_ids
    segment_id = _safe_review_string(segment.get("segment_id"))
    if segment_id:
        return [part for part in segment_id.split("--") if part]
    beat_number = segment.get("beat_number")
    return (
        [f"beat-{beat_number}"]
        if isinstance(beat_number, int) and not isinstance(beat_number, bool)
        else []
    )


def _review_label(beat_ids: list[str]) -> str:
    labels = []
    for beat_id in beat_ids:
        suffix = beat_id[5:] if beat_id.lower().startswith("beat-") else beat_id
        labels.append(f"Beat {suffix}")
    return " → ".join(labels)


def _serialize_prompt_review(
    project: str,
    project_dir: Path,
    manifest: Mapping[str, Any],
    stage: Any,
) -> dict[str, Any]:
    units = []
    for raw_entry in manifest.get("entries") or ():
        entry = _manifest_mapping(raw_entry)
        segment = _manifest_mapping(entry.get("segment"))
        summary = _project_review_value(
            entry.get("input_summary"), _INPUT_SUMMARY_SCHEMA
        )
        plan = _project_review_value(
            entry.get("director_plan"), _DIRECTOR_PLAN_SCHEMA
        )
        beat_ids = _review_beat_ids(entry, segment)
        first_frame = _safe_frame_reference(segment.get("first_frame"))
        last_frame = _safe_frame_reference(segment.get("last_frame"))
        mode = next(
            (
                value for value in (
                    summary.get("mode"), plan.get("mode"),
                    getattr(stage, "actual_mode", ""),
                )
                if isinstance(value, str) and value in {"auto", "i2va", "fl2va"}
            ),
            "fl2va" if last_frame else "i2va",
        )
        duration = _safe_duration(
            summary.get("duration_seconds"),
            entry.get("actual_duration_seconds"),
            segment.get("duration_seconds"),
        )
        units.append({
            "beat_ids": beat_ids,
            "label": _review_label(beat_ids),
            "mode": mode,
            "duration_seconds": duration,
            "first_frame_url": _asset_url(project, project_dir, first_frame),
            "last_frame_url": _asset_url(project, project_dir, last_frame),
            "director_plan": (
                plan if entry.get("director_plan") is not None else None
            ),
            "final_prompt": _safe_prompt(segment.get("prompt")),
            "prompt_profile": (
                _project_review_value(
                    entry.get("prompt_profile"), _PROMPT_PROFILE_SCHEMA
                )
                if entry.get("prompt_profile") is not None else None
            ),
            "quality_report": (
                _project_review_value(
                    entry.get("quality_report"), _QUALITY_REPORT_SCHEMA
                )
                if entry.get("quality_report") is not None else None
            ),
            "input_summary": summary,
            "workflow": _first_safe_review_string(
                entry.get("workflow_id"), manifest.get("workflow_id")
            ),
            "model": _first_safe_review_string(
                entry.get("model"), manifest.get("model"),
                getattr(stage, "actual_model", ""),
            ),
            "provider": _safe_review_string(
                getattr(stage, "actual_provider", "") or ""
            ),
            "provider_task_id": _first_safe_review_string(
                entry.get("provider_task_id"), manifest.get("provider_task_id")
            ),
        })
    def snapshot(name: str) -> dict[str, Any]:
        value = manifest.get(name)
        return dict(value) if isinstance(value, Mapping) else {}

    return {
        "workflow_parameters": snapshot("workflow_parameters"),
        "provider_parameters": snapshot("provider_parameters"),
        "actual_output": snapshot("actual_output"),
        "units": units,
    }


@router.get("/projects/{project}/episodes/{episode}/narrative-groups")
async def list_narrative_groups(
    project: str,
    episode: int,
    user: dict = Depends(get_api_user),
):
    resolved, groups, _ = await _resolve_groups(project, episode, user)
    return {"ok": True, "data": _serialize(project, resolved.project_dir, groups)}


@router.get(
    "/projects/{project}/episodes/{episode}/narrative-groups/"
    "{group_id}/video/prompts"
)
async def get_group_video_prompts(
    project: str,
    episode: int,
    group_id: str,
    user: dict = Depends(get_api_user),
):
    resolved = await resolve_project_scope(project, user, required_role="editor")
    try:
        manifest, stage = load_group_video_prompt_manifest(
            resolved.project_dir, episode, group_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Narrative group not found") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Video prompt manifest not found") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail="Video prompt manifest is invalid") from exc
    return {
        "ok": True,
        "data": _serialize_prompt_review(
            project, resolved.project_dir, manifest, stage
        ),
    }


@router.post("/projects/{project}/episodes/{episode}/narrative-groups/rebuild")
async def rebuild_narrative_groups(
    project: str,
    episode: int,
    user: dict = Depends(get_api_user),
):
    resolved, groups, _ = await _resolve_groups(project, episode, user, rebuild=True)
    return {"ok": True, "data": _serialize(project, resolved.project_dir, groups)}


@router.get(
    "/projects/{project}/episodes/{episode}/narrative-groups/"
    "{group_id}/{stage_name}/references"
)
async def preview_group_references(
    project: str,
    episode: int,
    group_id: str,
    stage_name: Literal["sketch", "render"],
    user: dict = Depends(get_api_user),
):
    resolved, groups, beats = await _resolve_groups(project, episode, user)
    try:
        _, selected_beats = _group_beats(groups, beats, group_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Narrative group not found") from exc
    preview = resolve_group_reference_preview(
        resolved.project_dir, selected_beats, stage=stage_name
    )
    return {
        "ok": True,
        "data": _serialize_reference_preview(project, resolved.project_dir, preview),
    }


@router.get(
    "/projects/{project}/episodes/{episode}/narrative-groups/"
    "{group_id}/{stage_name}/revisions"
)
async def list_stage_revisions(
    project: str,
    episode: int,
    group_id: str,
    stage_name: Literal["sketch", "render"],
    user: dict = Depends(get_api_user),
):
    resolved, groups, _ = await _resolve_groups(project, episode, user)
    group = next((item for item in groups if item.id == group_id), None)
    if group is None:
        raise HTTPException(status_code=404, detail="Narrative group not found")
    items = stage_history(resolved.project_dir, episode, group_id, stage_name)
    for item in items:
        item["grid_asset"] = _asset_url(
            project, resolved.project_dir, item.get("grid_asset", "")
        )
        for cell in item.get("cell_assets") or []:
            url = _asset_url(project, resolved.project_dir, str(cell.get("path") or ""))
            cell["path"] = url
            cell["url"] = url
    return {
        "ok": True,
        "data": {
            "items": items,
            "current_revision": group.stages[stage_name].revision,
        },
    }


@router.post(
    "/projects/{project}/episodes/{episode}/narrative-groups/"
    "{group_id}/{stage_name}/revisions/{revision}/rollback"
)
async def rollback_stage(
    project: str,
    episode: int,
    group_id: str,
    stage_name: Literal["sketch", "render"],
    revision: int,
    user: dict = Depends(get_api_user),
):
    resolved, _, _ = await _resolve_groups(project, episode, user)
    try:
        group = rollback_stage_revision(
            resolved.project_dir,
            episode,
            group_id,
            stage_name,
            revision=revision,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Narrative group revision not found") from exc
    return {"ok": True, "data": _serialize(project, resolved.project_dir, [group])[0]}


async def _enqueue_group_action(
    project: str,
    episode: int,
    group_id: str,
    stage: StageName,
    user: dict,
    *,
    regenerate: bool = False,
    split_only: bool = False,
    generation_request: NarrativeGroupGenerationRequest | None = None,
):
    resolved, groups, beats = await _resolve_groups(project, episode, user)
    try:
        source_group, selected_beats = _group_beats(groups, beats, group_id)
        selected_beats = generation_beats_for_group(
            resolved.project_dir,
            episode,
            group_id,
            selected_beats,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"Narrative group '{group_id}' not found"
        ) from exc

    request = generation_request or NarrativeGroupGenerationRequest()
    reference_selection = None
    if not split_only:
        preview = resolve_group_reference_preview(
            resolved.project_dir, selected_beats, stage=stage
        )
        try:
            apply_group_reference_selection(
                preview,
                use_style=request.use_style,
                selected_character_reference_ids=request.selected_character_reference_ids,
                selected_scene_reference_ids=request.selected_scene_reference_ids,
            )
        except UnknownGroupReferenceIds as exc:
            raise HTTPException(
                status_code=422,
                detail={"unknown_reference_ids": list(exc.unknown_ids)},
            ) from exc
        reference_selection = request.model_dump(
            exclude={
                "aspect_ratio",
                "provider_id",
                "model",
                "image_size",
                "allow_unconstrained",
            }
        )
    provider_id = model = ""
    image_size = "1K"
    constraint_mode = ""
    source_sketch_revision = 0
    source_sketch_asset = ""
    if not split_only:
        provider_id, model, image_size = _image_binding(
            resolved.ctx, resolved.project_dir, stage, request
        )
        if stage == "render":
            sketch = source_group.stages["sketch"]
            sketch_path = Path(sketch.grid_asset) if sketch.grid_asset else None
            sketch_ready = (
                sketch.status == "completed"
                and sketch.revision > 0
                and sketch_path is not None
                and sketch_path.is_file()
            )
            if not sketch_ready and not request.allow_unconstrained:
                raise HTTPException(
                    status_code=409,
                    detail="请先完成当前叙事组草图，或明确选择无草图约束生成",
                )
            if sketch_ready:
                constraint_mode = "strong_sketch"
                source_sketch_revision = sketch.revision
                source_sketch_asset = str(sketch_path)
            else:
                constraint_mode = "unconstrained"
    try:
        group, revision = advance_revision(
            resolved.project_dir,
            episode,
            group_id,
            stage,
            regenerate=regenerate,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Narrative group '{group_id}' not found") from exc

    scope = f"group_{group_id}_{stage}_r{revision}"
    mapping = [item.__dict__ for item in group.cell_to_beat]
    task_type = "narrative_group_split" if split_only else "narrative_group_grid"
    payload = {
        "episode": episode,
        "output_dir": resolved.output_dir,
        "project_dir": str(resolved.project_dir),
        "group_id": group_id,
        "stage": stage,
        "revision": revision,
        "layout": group.layout.__dict__,
        "aspect_ratio": request.aspect_ratio,
        "beat_ids": list(group.beat_ids),
        "cell_to_beat": mapping,
        "beats": selected_beats,
        "split_only": split_only,
    }
    if reference_selection is not None:
        payload["reference_selection"] = reference_selection
        payload.update(
            {
                "provider_id": provider_id,
                "model": model,
                "image_size": image_size,
                "constraint_mode": constraint_mode,
                "source_sketch_revision": source_sketch_revision,
                "source_sketch_asset": source_sketch_asset,
            }
        )
    queued = await get_task_backend().enqueue_project_task(
        resolved.ctx, task_type=task_type, queue_kind="default", episode=episode,
        scope=scope, payload=payload,
    )
    return {"ok": True, "data": {
        "task_id": queued.task_state.task_id, "scope": scope,
        "backend": queued.backend, "queue": queued.queue,
        "metadata": {"group_id": group_id, "stage": stage, "revision": revision},
    }}


async def _enqueue_group_video(
    project: str,
    episode: int,
    group_id: str,
    user: dict,
    request: NarrativeGroupVideoRequest,
    media_store: MediaCapabilityStore,
    credential_resolver: CredentialResolver,
    *,
    segment_id: str = "",
):
    registry = build_video_workflow_registry(media_store, credential_resolver)
    try:
        workflow = registry.resolve(request.model, VideoWorkflowScene.NARRATIVE_GROUP)
    except VideoWorkflowUnavailable as exc:
        raise HTTPException(
            status_code=422,
            detail="Video workflow is unavailable for narrative groups",
        ) from exc
    if request.mode not in workflow.supported_modes:
        raise HTTPException(
            status_code=422,
            detail="Video mode is unsupported by the selected workflow",
        )
    resolved, groups, beats = await _resolve_groups(project, episode, user)
    source_group = next((item for item in groups if item.id == group_id), None)
    if source_group is None:
        raise HTTPException(status_code=404, detail=f"Narrative group '{group_id}' not found")
    settings = source_group.video_settings
    if request.settings_revision is not None:
        if request.settings_revision != settings.revision:
            raise HTTPException(
                status_code=409,
                detail="Narrative group video settings revision is stale",
            )
        if workflow.id != settings.workflow_id:
            raise HTTPException(
                status_code=409,
                detail="Narrative group video workflow does not match saved settings",
            )
    reference_revision: int | None = None
    if workflow.reference_policy.required:
        reference_revision = source_group.video_reference_settings.revision
        if (
            request.reference_revision is None
            or request.reference_revision != reference_revision
        ):
            raise HTTPException(
                status_code=409,
                detail="Narrative group video reference revision is stale",
            )
        try:
            store = await make_sqlite_store_for_context(resolved.ctx)
            await resolve_saved_video_references(
                store=store,
                project_dir=resolved.project_dir,
                episode_number=episode,
                group=source_group,
                max_images=workflow.reference_policy.max_images,
            )
            from novelvideo.task_backend.runners.narrative_group_video import (
                _build_segments,
            )

            render = source_group.stages["render"]
            segments = _build_segments(
                {"mode": request.mode},
                generation_beats_for_group(
                    resolved.project_dir, episode, group_id, beats
                ),
                {
                    "beat_ids": list(source_group.beat_ids),
                    "cell_assets": list(render.cell_assets),
                    "video_plan": source_group.video_plan.to_dict(),
                },
            )
            if segment_id:
                segment_ids = [
                    str(item.get("id")) for item in source_group.video_segments
                ]
                segments = [segments[segment_ids.index(segment_id)]]
            for segment in segments:
                frame_paths = [segment.first_frame]
                if segment.last_frame:
                    frame_paths.append(segment.last_frame)
                if request.mode == "fl2va" and not segment.last_frame:
                    raise ValueError(
                        "MiniMax H3 fl2va mode requires a last frame"
                    )
                for frame_path in frame_paths:
                    if not frame_path or not Path(str(frame_path)).is_file():
                        raise ValueError("rendered video frame is unavailable")
                    if not _asset_url(
                        project, resolved.project_dir, str(frame_path)
                    ):
                        raise ValueError("rendered video frame path is unsafe")
        except (IndexError, OSError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        project_defaults = _project_video_workflow_defaults(resolved, workflow)
        parameter_overrides = dict(settings.overrides)
        if request.settings_revision is None and request.resolution is not None:
            parameter_overrides["resolution"] = request.resolution
        workflow_parameters = resolve_workflow_parameters(
            workflow,
            {**project_defaults, **parameter_overrides},
        )
    except VideoWorkflowParameterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        group, reservation = reserve_video_revision(
            resolved.project_dir, episode, group_id,
            expected_revision=request.revision,
            expected_plan_revision=request.plan_revision,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Narrative group '{group_id}' not found") from exc
    except RuntimeError:
        raise HTTPException(status_code=409, detail="Narrative group video revision is stale")
    revision = reservation.revision
    scope = f"group_{group_id}_video_r{revision}"
    payload = {
        "episode": episode,
        "group_id": group.id,
        "revision": revision,
        "plan_revision": request.plan_revision,
        "model": request.model,
        "mode": request.mode,
        "aspect_ratio": request.aspect_ratio,
        "workflow_parameters": workflow_parameters,
        "settings_revision": settings.revision,
        "segment_id": segment_id,
    }
    if reference_revision is not None:
        payload["reference_revision"] = reference_revision
    try:
        queued = await get_task_backend().enqueue_project_task(
            resolved.ctx, task_type="narrative_group_video", queue_kind="video",
            episode=episode, scope=scope, payload=payload,
        )
    except Exception as exc:
        restore_video_reservation(resolved.project_dir, episode, reservation)
        raise HTTPException(status_code=503, detail="Narrative group video queue is unavailable") from exc
    return {"ok": True, "data": {
        "task_id": queued.task_state.task_id, "scope": scope,
        "backend": queued.backend, "queue": queued.queue,
        "metadata": {"group_id": group_id, "stage": "video", "revision": revision},
    }}
@router.post(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/sketch/generate",
    status_code=status.HTTP_202_ACCEPTED,
)
@router.post(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/sketch-grid/generate",
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_sketch_group(
    project: str,
    episode: int,
    group_id: str,
    body: NarrativeGroupGenerationRequest | None = None,
    user: dict = Depends(get_api_user),
):
    return await _enqueue_group_action(
        project, episode, group_id, "sketch", user, generation_request=body
    )


@router.post(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/render/generate",
    status_code=status.HTTP_202_ACCEPTED,
)
@router.post(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/render-grid/generate",
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_render_group(
    project: str,
    episode: int,
    group_id: str,
    body: NarrativeGroupGenerationRequest | None = None,
    user: dict = Depends(get_api_user),
):
    return await _enqueue_group_action(
        project, episode, group_id, "render", user, generation_request=body
    )


@router.get(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/"
    "video/reference-preview"
)
async def get_group_video_reference_preview(
    project: str,
    episode: int,
    group_id: str,
    media_store: MediaCapabilityStore = Depends(get_media_capability_store),
    user: dict = Depends(get_api_user),
):
    resolved, groups, _ = await _resolve_groups(project, episode, user)
    group = next((item for item in groups if item.id == group_id), None)
    if group is None:
        raise HTTPException(status_code=404, detail="Narrative group not found")
    store = await make_sqlite_store_for_context(resolved.ctx)
    try:
        preview = await resolve_group_video_reference_preview(
            store=store,
            project_dir=resolved.project_dir,
            episode_number=episode,
            group=group,
            max_images=_video_reference_max_images(media_store),
        )
        data = _serialize_video_reference_preview(
            project, resolved.project_dir, episode, group_id, preview
        )
    except (OSError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "data": data}


@router.post(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/"
    "video/reference-uploads"
)
async def upload_group_video_reference(
    project: str,
    episode: int,
    group_id: str,
    file: UploadFile = File(...),
    media_store: MediaCapabilityStore = Depends(get_media_capability_store),
    user: dict = Depends(get_api_user),
):
    resolved, groups, _ = await _resolve_groups(project, episode, user)
    group = next((item for item in groups if item.id == group_id), None)
    if group is None:
        raise HTTPException(status_code=404, detail="Narrative group not found")
    limit = min(MAX_UPLOAD_BYTES, MAX_VIDEO_REFERENCE_BYTES)
    target: Path | None = None
    upload_id = ""
    try:
        content = await file.read(limit + 1)
        if len(content) > limit:
            raise ValueError("video reference upload exceeds the size limit")
        normalized = _normalize_video_reference_upload(
            content, str(file.content_type or "")
        )
        if len(normalized) > limit:
            raise ValueError(
                "normalized video reference upload exceeds the size limit"
            )
        upload_id = uuid.uuid4().hex
        target = write_temporary_video_reference(
            project_dir=resolved.project_dir,
            episode_number=episode,
            group_id=group_id,
            upload_id=upload_id,
            content=normalized,
        )
        store = await make_sqlite_store_for_context(resolved.ctx)
        preview = await resolve_group_video_reference_preview(
            store=store,
            project_dir=resolved.project_dir,
            episode_number=episode,
            group=group,
            max_images=_video_reference_max_images(media_store),
        )
        candidate = next(
            (
                item
                for item in preview.candidates
                if item.source_kind == "temporary_upload"
                and item.temporary_upload_id == upload_id
            ),
            None,
        )
        if candidate is None:
            raise ValueError("uploaded video reference is unavailable")
        data = _serialize_video_reference_candidate(
            project, resolved.project_dir, episode, group_id, candidate
        )
    except (ValueError, Image.DecompressionBombError) as exc:
        if target is not None:
            delete_temporary_video_reference(
                project_dir=resolved.project_dir,
                episode_number=episode,
                group_id=group_id,
                upload_id=upload_id,
                target=target,
            )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        if target is not None:
            delete_temporary_video_reference(
                project_dir=resolved.project_dir,
                episode_number=episode,
                group_id=group_id,
                upload_id=upload_id,
                target=target,
            )
        raise
    finally:
        await file.close()
    return {"ok": True, "data": data}


@router.put(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/"
    "video/references"
)
async def put_group_video_references(
    project: str,
    episode: int,
    group_id: str,
    request: UpdateVideoReferencesRequest,
    media_store: MediaCapabilityStore = Depends(get_media_capability_store),
    user: dict = Depends(get_api_user),
):
    resolved, _, _ = await _resolve_groups(project, episode, user)
    store = await make_sqlite_store_for_context(resolved.ctx)
    max_images = _video_reference_max_images(media_store)
    try:
        group = await update_video_reference_settings(
            store=store,
            project_dir=resolved.project_dir,
            episode_number=episode,
            group_id=group_id,
            expected_revision=request.expected_revision,
            selections=[
                VideoReferenceSelection(
                    reference_id=item.reference_id,
                    subject_description=item.subject_description,
                )
                for item in request.references
            ],
            max_images=max_images,
        )
        preview = await resolve_group_video_reference_preview(
            store=store,
            project_dir=resolved.project_dir,
            episode_number=episode,
            group=group,
            max_images=max_images,
        )
        data = _serialize_video_reference_preview(
            project, resolved.project_dir, episode, group_id, preview
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Narrative group not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (OSError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "data": data}


@router.put(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/video/settings"
)
async def put_group_video_settings(
    project: str,
    episode: int,
    group_id: str,
    request: NarrativeGroupVideoSettingsRequest,
    media_store: MediaCapabilityStore = Depends(get_media_capability_store),
    credential_resolver: CredentialResolver = Depends(get_media_credential_resolver),
    user: dict = Depends(get_api_user),
):
    resolved, _, _ = await _resolve_groups(project, episode, user)
    registry = build_video_workflow_registry(media_store, credential_resolver)
    try:
        workflow = registry.resolve(
            request.workflow_id, VideoWorkflowScene.NARRATIVE_GROUP
        )
        project_defaults = _project_video_workflow_defaults(resolved, workflow)
        resolved_values = resolve_workflow_parameters(
            workflow, {**project_defaults, **request.overrides}
        )
        normalized_overrides = {
            key: value
            for key, value in resolved_values.items()
            if project_defaults.get(key) != value
        }
        group = update_video_settings(
            resolved.project_dir,
            episode,
            group_id,
            expected_revision=request.expected_revision,
            workflow_id=workflow.id,
            overrides=normalized_overrides,
            project_defaults=project_defaults,
        )
    except VideoWorkflowUnavailable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except VideoWorkflowParameterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Narrative group not found") from exc
    except (RuntimeError, TypeError) as exc:
        status_code = 409 if isinstance(exc, RuntimeError) else 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return {
        "ok": True,
        "data": _serialize(project, resolved.project_dir, [group])[0],
    }


@router.put(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/video/plan"
)
async def put_group_video_plan(
    project: str,
    episode: int,
    group_id: str,
    request: NarrativeGroupVideoPlanRequest,
    user: dict = Depends(get_api_user),
):
    resolved, _, beats = await _resolve_groups(project, episode, user)
    try:
        plan_beats = generation_beats_for_group(
            resolved.project_dir, episode, group_id, beats
        )
        group = update_video_plan(
            resolved.project_dir,
            episode,
            group_id,
            plan_beats,
            expected_revision=request.expected_revision,
            units=[unit.model_dump() for unit in request.units],
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="Narrative group not found"
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "ok": True,
        "data": _serialize(project, resolved.project_dir, [group])[0],
    }


@router.post(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/video/generate",
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_video_group(
    project: str,
    episode: int,
    group_id: str,
    request: NarrativeGroupVideoRequest = Body(default_factory=NarrativeGroupVideoRequest),
    media_store: MediaCapabilityStore = Depends(get_media_capability_store),
    credential_resolver: CredentialResolver = Depends(get_media_credential_resolver),
    user: dict = Depends(get_api_user),
):
    return await _enqueue_group_video(
        project,
        episode,
        group_id,
        user,
        request,
        media_store,
        credential_resolver,
    )


@router.post(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/video/segments/{segment_id}/generate",
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_video_segment(
    project: str, episode: int, group_id: str, segment_id: str,
    request: NarrativeGroupVideoRequest = Body(default_factory=NarrativeGroupVideoRequest),
    media_store: MediaCapabilityStore = Depends(get_media_capability_store),
    credential_resolver: CredentialResolver = Depends(get_media_credential_resolver),
    user: dict = Depends(get_api_user),
):
    _, groups, _ = await _resolve_groups(project, episode, user)
    group = next((item for item in groups if item.id == group_id), None)
    if group is None:
        raise HTTPException(status_code=404, detail="Narrative group not found")
    if segment_id not in {str(item.get("id")) for item in group.video_segments}:
        raise HTTPException(status_code=404, detail="Video segment not found")
    return await _enqueue_group_video(
        project, episode, group_id, user, request, media_store,
        credential_resolver, segment_id=segment_id,
    )


@router.put(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/style"
)
async def put_group_style(
    project: str, episode: int, group_id: str,
    request: NarrativeGroupStyleRequest,
    user: dict = Depends(get_api_user),
):
    resolved, groups, _ = await _resolve_groups(project, episode, user)
    if not any(item.id == group_id for item in groups):
        raise HTTPException(status_code=404, detail="Narrative group not found")
    from novelvideo.services.style_service import StyleService
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.project_config import load_project_config_from_state_dir

    config = load_project_config_from_state_dir(
        getattr(resolved.ctx, "state_dir", resolved.project_dir),
        username=resolved.ctx.owner_username,
        project=resolved.ctx.project_name,
    )
    project_style = str(config.get("visual_style") or "chinese_period_drama")
    snapshot = StyleService.resolve_style_snapshot(
        project_style, request.style_id, username=resolved.ctx.owner_username,
        project=resolved.ctx.project_name, project_dir=resolved.project_dir,
    )
    store = DirectorPlanStore(resolved.project_dir)
    active = store.load_active(episode)
    if active is None:
        raise HTTPException(status_code=409, detail="Active director plan required")
    child = active.new(
        episode=active.episode, source_script_hash=active.source_script_hash,
        director_model=active.director_model, prompt_version=active.prompt_version,
        project_style_snapshot_id=snapshot.snapshot_id,
        project_style_snapshot=snapshot,
        groups=tuple(
            item.model_copy(update={"style_snapshot_id": snapshot.snapshot_id})
            if item.id == group_id else item for item in active.groups
        ),
        parent_revision_id=active.revision_id,
    ).model_copy(update={
        "status": "review_required", "validation_report": active.validation_report
    })
    store.save(child)
    if request.action == "restyle":
        child = store.activate(episode, child.revision_id)
    task_data: dict[str, Any] = {}
    if request.action == "redirect":
        source_store = EpisodeSourceStore(
            await make_sqlite_store_for_context(resolved.ctx)
        )
        source = next(
            (
                item for item in await source_store.list_sources()
                if int(item.episode_number) == episode
            ),
            None,
        )
        if source is None:
            raise HTTPException(status_code=404, detail="Episode source not found")
        scope = f"style:{snapshot.style_hash}:revision:{source.source_revision}"
        queued = await get_task_backend().enqueue_project_task(
            resolved.ctx,
            task_type="director_plan",
            queue_kind="default",
            episode=episode,
            scope=scope,
            payload={
                "project_id": str(resolved.ctx.project_id),
                "episode": episode,
                "source_revision": int(source.source_revision),
                "style_id": snapshot.style_id,
                "style_snapshot_id": snapshot.snapshot_id,
            },
        )
        task_data = {
            "task_id": queued.task_state.task_id,
            "backend": queued.backend,
            "queue": queued.queue,
            "scope": scope,
        }
    return {"ok": True, "data": {
        "revision_id": child.revision_id, "status": child.status,
        "action": request.action,
        "style_snapshot": snapshot.model_dump(mode="json"),
        **task_data,
    }}


@router.post(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/video/dialogue-source",
    status_code=status.HTTP_202_ACCEPTED,
)
async def change_group_video_dialogue_source(
    project: str,
    episode: int,
    group_id: str,
    request: NarrativeGroupDialogueSourceRequest,
    user: dict = Depends(get_api_user),
):
    resolved, groups, _ = await _resolve_groups(project, episode, user)
    try:
        update_video_manifest_dialogue_source(
            resolved.project_dir,
            episode,
            group_id,
            span_index=request.span_index,
            dialogue_source=request.dialogue_source,
            expected_revision=request.revision,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Narrative group not found") from exc
    except (FileNotFoundError, IndexError) as exc:
        raise HTTPException(status_code=404, detail="Narrative group video span not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    revision = request.revision
    scope = f"group_{group_id}_video_compose_r{revision}_s{request.span_index}"
    payload = {
        "episode": episode,
        "group_id": group_id,
        "revision": revision,
        "span_index": request.span_index,
        "dialogue_source": request.dialogue_source,
    }
    queued = await get_task_backend().enqueue_project_task(
        resolved.ctx,
        task_type="narrative_group_video_compose",
        queue_kind="default",
        episode=episode,
        scope=scope,
        payload=payload,
    )
    return {"ok": True, "data": {
        "task_id": queued.task_state.task_id,
        "scope": scope,
        "backend": queued.backend,
        "queue": queued.queue,
        "metadata": {"group_id": group_id, "stage": "video", "revision": revision},
    }}


@router.post(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/{stage_name}/split",
    status_code=status.HTTP_202_ACCEPTED,
)
async def split_group(
    project: str,
    episode: int,
    group_id: str,
    stage_name: Literal["sketch", "render"],
    body: NarrativeGroupGenerationRequest | None = None,
    user: dict = Depends(get_api_user),
):
    return await _enqueue_group_action(
        project,
        episode,
        group_id,
        stage_name,
        user,
        split_only=True,
        generation_request=body,
    )


@router.post(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/{stage_name}/regenerate",
    status_code=status.HTTP_202_ACCEPTED,
)
async def regenerate_group(
    project: str,
    episode: int,
    group_id: str,
    stage_name: Literal["sketch", "render"],
    body: NarrativeGroupGenerationRequest | None = None,
    user: dict = Depends(get_api_user),
):
    return await _enqueue_group_action(
        project,
        episode,
        group_id,
        stage_name,
        user,
        regenerate=True,
        generation_request=body,
    )
