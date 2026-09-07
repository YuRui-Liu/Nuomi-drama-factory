"""Narrative-group aggregate API.

The sidecar is the server-side source of truth. Clients never submit their own
cell mapping, which prevents refreshes and retries from silently reshuffling a
grid.
"""

from __future__ import annotations

import hashlib
import math
import re
import uuid
from dataclasses import asdict
from io import BytesIO
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal, Mapping
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from novelvideo.api.auth import get_api_user
from novelvideo.api.schemas import NarrativeReferenceResolutionRequest
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
from novelvideo.media_capabilities.video.h3_reference_runtime import (
    bind_h3_reference_snapshot_owner,
    freeze_h3_reference_frames,
    persist_h3_reference_input_snapshot,
    retain_h3_reference_snapshot,
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
    resolve_requirement_reference_preview,
)
from novelvideo.narrative_groups.reference_requirements import (
    reference_requirements_for_shots,
)
from novelvideo.narrative_groups.reference_decisions import (
    InvalidReferenceDecisions,
    ResolvedProjectAsset,
    build_reference_snapshot,
)
from novelvideo.narrative_groups.reference_matching import ReferenceMatchPreview
from novelvideo.narrative_groups.reference_uploads import (
    InvalidReferenceUpload,
    load_reference_upload,
    save_reference_upload,
)
from novelvideo.utils.path_resolver import (
    canonical_identity_path,
    canonical_portrait_path,
    canonical_prop_reference_path,
    canonical_scene_master_path,
)
from novelvideo.narrative_groups.service import (
    advance_revision,
    generation_beats_for_group,
    load_effective_groups,
    load_group_video_prompt_manifest,
    load_materialized_groups,
    narrative_group_sidecar_guard,
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
from novelvideo.media_capabilities.video.h3_timeline import (
    H3DirectorOutputManifest,
    H3ObservedBoundary,
    load_h3_director_manifest,
    save_h3_director_manifest,
    source_shot_ids_for,
)
from novelvideo.ports import get_task_backend
from novelvideo.shot_continuity import (
    ContinuityRevisionConflict,
    ShotContinuityContract,
    ShotContinuityStore,
)
from novelvideo.task_state import (
    ACTIVE_PROJECT_TASK_STATUSES,
    TERMINAL_TASK_STATUSES,
    get_task_manager,
)
from novelvideo.utils.upload_safety import MAX_UPLOAD_BYTES

router = APIRouter()


def _reference_enqueue_ownership(
    *, ctx, episode: int, scope: str, snapshot_id: str, snapshot_digest: str
) -> Literal["owned", "unowned", "unknown"]:
    """Classify durable ownership without treating corrupt state as absence."""
    try:
        task = get_task_manager().get_task_for_project(
            ctx,
            "narrative_group_video",
            episode,
            scope=scope,
        )
    except Exception:
        return "unknown"
    if task is None:
        return "unowned"
    try:
        raw_metadata = task.metadata
        if isinstance(raw_metadata, Mapping):
            metadata = dict(raw_metadata)
        elif raw_metadata is None and isinstance(task.result, Mapping):
            nested_metadata = task.result.get("task_metadata")
            if not isinstance(nested_metadata, Mapping):
                return "unknown"
            metadata = dict(nested_metadata)
        else:
            return "unknown"
        task_status = task.status
        if not isinstance(task_status, str):
            return "unknown"
        if (
            task_status not in ACTIVE_PROJECT_TASK_STATUSES
            and task_status not in TERMINAL_TASK_STATUSES
            and task_status != "retryable"
        ):
            return "unknown"
        persisted_id = metadata.get("reference_snapshot_id")
        persisted_digest = metadata.get("reference_snapshot_digest")
        if persisted_id is None or persisted_digest is None:
            return "unknown"
        if not isinstance(persisted_id, str) or not isinstance(
            persisted_digest, str
        ):
            return "unknown"
        if persisted_id != snapshot_id or persisted_digest != snapshot_digest:
            return "unowned"
        if (
            task_status in ACTIVE_PROJECT_TASK_STATUSES
            or task_status == "retryable"
            or metadata.get("retryable") is True
        ):
            return "owned"
        if task_status in TERMINAL_TASK_STATUSES:
            return "unowned"
        return "unknown"
    except Exception:
        return "unknown"


class NarrativeGroupGenerationRequest(BaseModel):
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
    use_style: bool = True
    selected_character_reference_ids: list[str] | None = None
    selected_scene_reference_ids: list[str] | None = None
    provider_id: str | None = None
    model: str | None = None
    image_size: Literal["1K", "2K", "4K"] | None = None
    allow_unconstrained: bool = False
    reference_resolution: NarrativeReferenceResolutionRequest | None = None


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


class NarrativeGroupContinuityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_revision: int = Field(ge=1)
    observed_carry_out: str = Field(min_length=1, max_length=2000)
    accept_deviation: bool = False
    deviation_reason: str = Field(default="", max_length=2000)
    lock_violations: tuple[
        Literal["identity", "spatial", "prop", "camera", "lighting"], ...
    ] = ()

    @field_validator("observed_carry_out", "deviation_reason", mode="before")
    @classmethod
    def trim_continuity_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def require_explained_deviation(self) -> "NarrativeGroupContinuityRequest":
        if self.accept_deviation and not self.deviation_reason:
            raise ValueError("accepted deviation requires a nonblank reason")
        return self


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
) -> dict[str, Any]:
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
        "beat_ids": list(candidate.beat_ids),
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
        item["title"] = str(
            item.get("objective") or item.get("visible_turn") or ""
        ).strip() or None
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


def _serialize_requirement_reference_preview(
    project: str, project_dir: Path, preview: Any
) -> dict[str, Any]:
    def binding_item(binding: Any) -> dict[str, Any]:
        return {
            "requirement_id": binding.requirement_id,
            "decision": binding.decision,
            "asset_id": binding.asset_id,
            "asset_kind": binding.asset_kind,
            "thumbnail_url": _asset_url(project, project_dir, binding.image_path),
        }

    requirements = []
    for requirement in preview.requirements:
        requirements.append({
            "id": requirement.id,
            "kind": requirement.kind,
            "entity_id": requirement.entity_id,
            "base_entity_id": requirement.base_entity_id or None,
            "variant_id": requirement.variant_id or None,
            "shot_ids": list(requirement.shot_ids),
            "required": requirement.required,
            "label": requirement.label,
            "status": requirement.status,
            "candidate_asset_ids": list(requirement.candidate_asset_ids),
            "available_actions": list(requirement.available_actions),
            "bindings": [binding_item(item) for item in requirement.bindings],
            "warning": requirement.warning or None,
        })
    bindings = [binding_item(item) for item in preview.bindings]
    selected = [item for item in bindings if item["thumbnail_url"]]
    legacy = {"character_references": [], "scene_references": []}
    by_requirement = {item["id"]: item for item in requirements}
    for item in selected:
        requirement = by_requirement.get(item["requirement_id"], {})
        kind = str(requirement.get("kind") or "")
        legacy_kind = "character" if kind == "character_identity" else "scene" if kind.startswith("scene_") else ""
        if not legacy_kind:
            continue
        legacy[f"{legacy_kind}_references"].append({
            "id": item["asset_id"],
            "kind": legacy_kind,
            "source_kind": item["asset_kind"],
            "label": requirement.get("label") or item["asset_id"],
            "thumbnail_url": item["thumbnail_url"],
            "beat_numbers": [],
            "enabled_by_default": True,
            "warning": requirement.get("warning"),
        })
    return {
        "requirements": requirements,
        "bindings": bindings,
        "style": {
            "id": preview.style.id,
            "label": preview.style.name,
            "prompt": preview.style.prompt,
            "enabled_by_default": True,
            "warning": preview.style.warning,
        },
        **legacy,
        "limits": {
            "max_images": MAX_GROUP_IMAGE_REFERENCES,
            "selected_images": len(selected),
            "omitted_reference_ids": [],
        },
        "warnings": list(preview.warnings),
    }


def _active_reference_requirements(
    project_dir: Path, episode: int, group_id: str
) -> tuple[Any, ...]:
    active = DirectorPlanStore(project_dir).load_active(episode)
    group = next(
        (item for item in active.groups if item.id == group_id), None
    ) if active is not None else None
    return reference_requirements_for_shots(group.shots) if group is not None else ()


async def _project_reference_assets(
    store: Any, project_dir: Path
) -> dict[str, ResolvedProjectAsset]:
    """Return opaque project-scoped candidate IDs mapped to safe image assets."""
    result: dict[str, ResolvedProjectAsset] = {}
    def add(
        kind: str, entity_id: str, path: Path, *, base: str = "", variant: str = ""
    ) -> None:
        if not path.is_file():
            return
        asset_id = hashlib.sha256(
            f"{kind}\0{entity_id}\0{base}\0{variant}".encode("utf-8")
        ).hexdigest()
        result[asset_id] = ResolvedProjectAsset(
            asset_id=asset_id, image_path=str(path.resolve()), asset_kind=kind,
            entity_id=entity_id, base_entity_id=base, variant_id=variant,
        )

    for character in await store.list_characters():
        for identity in getattr(character, "identities", ()) or ():
            identity_id = str(getattr(identity, "identity_id", "") or "").strip()
            if identity_id:
                add(
                    "character_identity", identity_id,
                    canonical_identity_path(project_dir, character.name, identity_id),
                )
    for scene in await store.list_scenes():
        name = str(getattr(scene, "name", "") or "").strip()
        base = str(getattr(scene, "base_scene_id", "") or "").strip()
        variant = str(getattr(scene, "variant_id", "") or "").strip()
        if name:
            add(
                "scene_variant" if base else "scene_base", name,
                canonical_scene_master_path(project_dir, name),
                base=base, variant=variant,
            )
    for prop in await store.list_props():
        name = str(getattr(prop, "name", "") or "").strip()
        if name:
            add("prop", name, canonical_prop_reference_path(project_dir, name))
    return result


async def _reference_persistence_target(
    store: Any,
    project_dir: Path,
    *,
    requirement_id: str,
    asset_kind: str,
    target_entity_id: str,
    base_entity_id: str,
    variant_id: str,
) -> Path:
    target = target_entity_id.strip()
    if asset_kind == "prop":
        if (requirement_id and requirement_id != f"prop:{target}") or not target:
            raise HTTPException(status_code=422, detail="Invalid prop persistence target")
        if await store.get_prop(target) is None:
            raise HTTPException(status_code=422, detail="Target prop does not exist")
        return canonical_prop_reference_path(project_dir, target)
    if asset_kind == "scene_base":
        if (requirement_id and requirement_id != f"scene_base:{target}") or not target:
            raise HTTPException(status_code=422, detail="Invalid scene persistence target")
        scene = await store.get_scene_exact(target)
        if scene is None or str(getattr(scene, "base_scene_id", "") or "").strip():
            raise HTTPException(status_code=422, detail="Target base scene does not exist")
        return canonical_scene_master_path(project_dir, target)
    if asset_kind == "scene_variant":
        base = base_entity_id.strip()
        variant = variant_id.strip()
        if (
            not target or not base or not variant
            or (requirement_id and requirement_id != f"scene_variant:{base}:{variant}")
        ):
            raise HTTPException(status_code=422, detail="Invalid scene variant target")
        scene = await store.get_scene_exact(target)
        if (
            scene is None
            or str(getattr(scene, "base_scene_id", "") or "").strip() != base
            or str(getattr(scene, "variant_id", "") or "").strip() != variant
        ):
            raise HTTPException(status_code=422, detail="Target scene variant does not exist")
        return canonical_scene_master_path(project_dir, target)
    if asset_kind == "character_identity":
        if (requirement_id and requirement_id != f"character_identity:{target}") or not target:
            raise HTTPException(status_code=422, detail="Invalid identity persistence target")
        characters = await store.list_characters()
        character = next((
            item for item in characters
            if any(
                str(getattr(identity, "identity_id", "") or "").strip() == target
                for identity in (getattr(item, "identities", ()) or ())
            )
        ), None)
        if character is None:
            raise HTTPException(status_code=422, detail="Target identity does not exist")
        return canonical_identity_path(project_dir, character.name, target)
    raise HTTPException(status_code=422, detail="Unsupported persistence asset kind")


_REJECTED_REVIEW_VALUE = object()
_SAFE_TEXT = object()
_SAFE_INT = object()
_SAFE_NUMBER = object()
_SAFE_BOOL = object()
_SAFE_OPTIONAL_TEXT = object()
_SAFE_OPTIONAL_INT = object()
_MAX_REVIEW_ID_LENGTH = 256
_MAX_REVIEW_TEXT_LENGTH = 16 * 1024
_MAX_REVIEW_PROMPT_LENGTH = 256 * 1024
_MAX_REVIEW_BEAT_IDS = 32
_URI_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_SENSITIVE_SNAPSHOT_KEY_PARTS = (
    "authorization", "apikey", "token", "secret", "password", "cookie",
    "credential", "accesskey", "privatekey",
)
_PATH_SNAPSHOT_KEY_PARTS = ("path", "file", "dir", "folder", "uri", "url")
_EMBEDDED_PATH_OR_URI_RE = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9+.-]*://|(?:^|[\s=([{,:;'\"<`])/\S+|"
    r"[A-Za-z]:[\\/]\S+|\\\\\S+|"
    r"(?:^|[\s=([{,:;'\"<`])(?:[^/\\\s]+[\\/])+"
    r"[^/\\\s]+\.[A-Za-z0-9]{1,16}(?=$|[\s)\]}>,'\";:`]))"
)
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
_OBSERVED_BOUNDARY_SCHEMA = {
    "value": _SAFE_TEXT,
    "source_contract_revision": _SAFE_INT,
    "result_contract_revision": _SAFE_INT,
    "accepted": _SAFE_BOOL,
    "deviation_reason": _SAFE_TEXT,
    "lock_violations": [_SAFE_TEXT],
}
_CONTINUITY_BOUNDARY_SCHEMA = {
    "carry_in": _SAFE_TEXT,
    "planned_carry_out": _SAFE_TEXT,
    "observed_carry_out": _SAFE_OPTIONAL_TEXT,
    "deviation_accepted": _SAFE_BOOL,
    "deviation_reason": _SAFE_TEXT,
}
_CONTINUITY_CONTRACT_SCHEMA = {
    "revision": _SAFE_INT,
    "shot_id": _SAFE_TEXT,
    "scene_id": _SAFE_TEXT,
    "predecessor_shot_id": _SAFE_OPTIONAL_TEXT,
    "predecessor_revision": _SAFE_OPTIONAL_INT,
    "boundary": _CONTINUITY_BOUNDARY_SCHEMA,
}
_RISK_DIMENSION_SCHEMA = {
    "dimension": _SAFE_TEXT,
    "level": _SAFE_INT,
    "reasons": [_SAFE_TEXT],
}
_RISK_REPORT_SCHEMA = {
    "spatial": _RISK_DIMENSION_SCHEMA,
    "identity": _RISK_DIMENSION_SCHEMA,
    "motion": _RISK_DIMENSION_SCHEMA,
    "continuity": _RISK_DIMENSION_SCHEMA,
    "blockers": [_SAFE_TEXT],
}
_MODE_DECISION_SCHEMA = {
    "requested": _SAFE_TEXT,
    "mode": _SAFE_OPTIONAL_TEXT,
    "reason_codes": [_SAFE_TEXT],
    "blockers": [_SAFE_TEXT],
}
_COMPILED_BUNDLE_SCHEMA = {
    "adapter": _SAFE_TEXT,
    "mode": _SAFE_TEXT,
    "compiler_id": _SAFE_TEXT,
    "compiler_version": _SAFE_INT,
    "diagnostics": [_SAFE_TEXT],
    "bundle_sha256": _SAFE_TEXT,
}


def _manifest_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    return dict(dump(mode="json")) if callable(dump) else {}


def _project_review_value(value: Any, schema: Any) -> Any:
    if schema is _SAFE_OPTIONAL_TEXT:
        return None if value is None else _project_review_value(value, _SAFE_TEXT)
    if schema is _SAFE_OPTIONAL_INT:
        return None if value is None else _project_review_value(value, _SAFE_INT)
    if schema is _SAFE_TEXT:
        if (
            isinstance(value, str)
            and len(value) <= _MAX_REVIEW_TEXT_LENGTH
            and not _is_path_or_uri(value)
            and not _EMBEDDED_PATH_OR_URI_RE.search(value)
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


def _is_path_snapshot_key(key: str) -> bool:
    folded = key.casefold()
    tokens = {token for token in re.split(r"[^a-z0-9]+", folded) if token}
    return bool(tokens.intersection(_PATH_SNAPSHOT_KEY_PARTS)) or folded.endswith(
        ("_path", "_file", "_dir")
    )


def _sanitize_manifest_snapshot(value: Any, *, path_context: bool = False) -> Any:
    if isinstance(value, Mapping):
        result = {}
        for raw_key, raw_child in value.items():
            if not isinstance(raw_key, str):
                continue
            normalized_key = "".join(
                character for character in raw_key.casefold() if character.isalnum()
            )
            if any(
                sensitive in normalized_key
                for sensitive in _SENSITIVE_SNAPSHOT_KEY_PARTS
            ) or normalized_key in {"auth", "session"}:
                continue
            child = _sanitize_manifest_snapshot(
                raw_child,
                path_context=path_context or _is_path_snapshot_key(raw_key),
            )
            if child is not _REJECTED_REVIEW_VALUE:
                result[raw_key] = child
        return result
    if isinstance(value, (list, tuple)):
        return [
            child
            for item in value
            if (child := _sanitize_manifest_snapshot(item, path_context=path_context))
            is not _REJECTED_REVIEW_VALUE
        ]
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else _REJECTED_REVIEW_VALUE
    if isinstance(value, str):
        if path_context and value.strip():
            return "[redacted]"
        if _EMBEDDED_PATH_OR_URI_RE.search(value):
            return _REJECTED_REVIEW_VALUE
        return _project_review_value(value, _SAFE_TEXT)
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
    if not isinstance(value, str) or len(value) > 2048:
        return ""
    stripped = value.strip()
    if (
        _URI_RE.match(stripped)
        or stripped.casefold().startswith("file://")
        or stripped.startswith(("\\\\", "//"))
        or PureWindowsPath(stripped).drive
        or PureWindowsPath(stripped).is_absolute()
    ):
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
    source_shot_ids = [
        safe
        for value in (segment.get("source_shot_ids") or ())[:_MAX_REVIEW_BEAT_IDS]
        if (safe := _safe_review_string(value))
    ]
    if source_shot_ids:
        return source_shot_ids
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
        continuity_contracts = entry.get("continuity_contracts") or ()
        terminal_contract = _manifest_mapping(
            continuity_contracts[-1]
            if isinstance(continuity_contracts, (list, tuple))
            and continuity_contracts
            else None
        )
        terminal_boundary = _manifest_mapping(terminal_contract.get("boundary"))
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
            "segment_id": _safe_review_string(segment.get("segment_id")),
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
            "continuity_contracts": _project_review_value(
                continuity_contracts, [_CONTINUITY_CONTRACT_SCHEMA]
            ),
            "risk_report": (
                _project_review_value(entry.get("risk_report"), _RISK_REPORT_SCHEMA)
                if entry.get("risk_report") is not None
                else None
            ),
            "mode_decision": (
                _project_review_value(
                    entry.get("mode_decision"), _MODE_DECISION_SCHEMA
                )
                if entry.get("mode_decision") is not None
                else None
            ),
            "compiled_bundle": (
                _project_review_value(
                    entry.get("compiled_bundle"), _COMPILED_BUNDLE_SCHEMA
                )
                if entry.get("compiled_bundle") is not None
                else None
            ),
            "planned_carry_out": _safe_review_string(
                terminal_boundary.get("planned_carry_out"), max_length=2000
            ),
            "observed_carry_out": (
                _project_review_value(
                    entry.get("observed_carry_out"), _OBSERVED_BOUNDARY_SCHEMA
                )
                if entry.get("observed_carry_out") is not None
                else None
            ),
        })
    def snapshot(name: str) -> dict[str, Any]:
        value = manifest.get(name)
        sanitized = _sanitize_manifest_snapshot(value)
        return sanitized if isinstance(sanitized, dict) else {}

    workflow_id = _safe_review_string(manifest.get("workflow_id"))
    provider_workflow_id = _safe_review_string(
        manifest.get("provider_workflow_id")
    )
    reference_revision = manifest.get("reference_settings_revision")
    if (
        isinstance(reference_revision, bool)
        or not isinstance(reference_revision, int)
        or reference_revision < 0
    ):
        reference_revision = None
    reference_limit = manifest.get("reference_limit")
    if (
        isinstance(reference_limit, bool)
        or not isinstance(reference_limit, int)
        or not 1 <= reference_limit <= 10
    ):
        reference_limit = None
    global_references = []
    raw_global_references = manifest.get("global_references")
    if not isinstance(raw_global_references, (list, tuple)):
        raw_global_references = ()
    for raw_reference in raw_global_references[:10]:
        reference = _manifest_mapping(raw_reference)
        picture_index = reference.get("picture_index")
        reference_id = _safe_review_string(reference.get("reference_id"))
        source_kind = reference.get("source_kind")
        label = _safe_review_string(reference.get("label"))
        description = _safe_review_string(
            reference.get("subject_description"), max_length=500
        )
        sha256 = reference.get("sha256")
        if not (
            isinstance(picture_index, int)
            and not isinstance(picture_index, bool)
            and picture_index >= 1
            and reference_id
            and source_kind in {
                "character_identity", "scene_master", "prop_reference",
                "temporary_upload",
            }
            and label
            and description
            and isinstance(sha256, str)
            and re.fullmatch(r"[0-9a-f]{64}", sha256)
        ):
            continue
        global_references.append({
            "picture_index": picture_index,
            "reference_id": reference_id,
            "source_kind": source_kind,
            "label": label,
            "subject_description": description,
            "sha256": sha256,
        })

    return {
        "format_version": (
            manifest.get("format_version")
            if isinstance(manifest.get("format_version"), int)
            and not isinstance(manifest.get("format_version"), bool)
            else 1
        ),
        "workflow_id": workflow_id or None,
        "provider_workflow_id": provider_workflow_id or None,
        "reference_settings_revision": reference_revision,
        "reference_limit": reference_limit,
        "global_references": global_references,
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


@router.put(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/"
    "video/segments/{segment_id}/continuity"
)
async def put_group_video_segment_continuity(
    project: str,
    episode: int,
    group_id: str,
    segment_id: str,
    request: NarrativeGroupContinuityRequest,
    user: dict = Depends(get_api_user),
):
    resolved = await resolve_project_scope(project, user, required_role="editor")
    root = Path(resolved.project_dir).resolve()
    with narrative_group_sidecar_guard(root, episode):
        expected_stage = _load_current_video_stage(root, episode, group_id)
        if expected_stage.status not in _POSTFLIGHT_STAGE_STATUSES:
            raise HTTPException(
                status_code=409, detail="Video stage is not ready for review"
            )
        expected_fingerprint = _postflight_stage_fingerprint(root, expected_stage)
        stage = _load_postflight_stage(root, episode, group_id)
        if _postflight_stage_fingerprint(root, stage) != expected_fingerprint:
            raise HTTPException(
                status_code=409, detail="Narrative group video stage changed"
            )
        return _put_group_video_segment_continuity_locked(
            project, root, episode, segment_id, request, stage
        )


_POSTFLIGHT_STAGE_STATUSES = {
    "review",
    "completed",
    "partial_failure",
    "failed",
}
_POSTFLIGHT_MANIFEST_STATUSES = {
    "completed",
    "partial_failure",
    "quality_rejected",
    "transport_failed",
    "postprocess_failed",
    "quality_mismatch",
}


def _load_postflight_stage(project_dir: Path, episode: int, group_id: str):
    try:
        _, stage = load_group_video_prompt_manifest(project_dir, episode, group_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Narrative group not found") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Video manifest not found") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail="Video manifest is invalid") from exc
    return stage


def _load_current_video_stage(project_dir: Path, episode: int, group_id: str):
    group = next(
        (
            item
            for item in load_materialized_groups(project_dir, episode)
            if item.id == group_id
        ),
        None,
    )
    if group is None:
        raise HTTPException(status_code=404, detail="Narrative group not found")
    return group.stages["video"]


def _postflight_manifest_path(project_dir: Path, stage: Any) -> Path:
    stored_path = Path(str(stage.manifest_asset).strip())
    candidate = stored_path if stored_path.is_absolute() else project_dir / stored_path
    manifest_path = candidate.resolve()
    if not manifest_path.is_relative_to(project_dir) or not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="Video manifest not found")
    return manifest_path


def _postflight_stage_fingerprint(project_dir: Path, stage: Any) -> tuple[int, str, str]:
    stored_path = Path(str(stage.manifest_asset).strip())
    candidate = stored_path if stored_path.is_absolute() else project_dir / stored_path
    manifest_path = candidate.resolve()
    return (
        int(stage.revision),
        str(stage.status),
        str(manifest_path),
    )


def _put_group_video_segment_continuity_locked(
    project: str,
    root: Path,
    episode: int,
    segment_id: str,
    request: NarrativeGroupContinuityRequest,
    stage: Any,
):
    if stage.status not in _POSTFLIGHT_STAGE_STATUSES:
        raise HTTPException(status_code=409, detail="Video stage is not ready for review")

    manifest_path = _postflight_manifest_path(root, stage)
    try:
        manifest = load_h3_director_manifest(manifest_path)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="Video manifest is invalid") from exc
    if (
        manifest.format_version < 2
        or manifest.status not in _POSTFLIGHT_MANIFEST_STATUSES
    ):
        raise HTTPException(
            status_code=409, detail="Video manifest is not terminal review evidence"
        )

    matches = [
        (index, entry)
        for index, entry in enumerate(manifest.entries)
        if entry.segment.segment_id == segment_id
    ]
    if not matches:
        raise HTTPException(status_code=404, detail="Video segment not found")
    if len(matches) != 1:
        raise HTTPException(status_code=409, detail="Video segment evidence is ambiguous")
    entry_index, entry = matches[0]
    if entry.status not in _POSTFLIGHT_MANIFEST_STATUSES:
        raise HTTPException(
            status_code=409, detail="Video segment is not terminal review evidence"
        )
    if not entry.continuity_contracts:
        raise HTTPException(status_code=409, detail="Continuity contract evidence is missing")
    try:
        contract = ShotContinuityContract.model_validate(
            entry.continuity_contracts[-1]
        )
    except ValidationError as exc:
        raise HTTPException(status_code=409, detail="Continuity contract evidence is invalid") from exc
    if contract.revision != request.contract_revision:
        raise HTTPException(status_code=409, detail="Continuity contract revision is stale")
    if contract.shot_id != source_shot_ids_for(entry.segment)[-1]:
        raise HTTPException(status_code=409, detail="Continuity contract does not match video segment")

    planned = contract.boundary.planned_carry_out
    is_deviation = request.observed_carry_out != planned
    if is_deviation and not request.accept_deviation:
        raise HTTPException(status_code=409, detail="Observed boundary deviates from the plan")
    reason = request.deviation_reason if request.accept_deviation else ""
    boundary = contract.boundary.model_copy(update={
        "observed_carry_out": request.observed_carry_out,
        "deviation_accepted": request.accept_deviation,
        "deviation_reason": reason,
    })
    candidate = contract.model_copy(update={"boundary": boundary})
    continuity_store = ShotContinuityStore(root)
    try:
        active = continuity_store.load_active(episode, contract.shot_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Continuity contract evidence is invalid") from exc
    candidate_semantics = candidate.model_copy(update={"revision": 0})
    if (
        active is not None
        and active.revision == request.contract_revision
        and active.contract_sha256 == contract.contract_sha256
    ):
        try:
            saved = continuity_store.put(
                episode, candidate, expected_revision=request.contract_revision
            )
        except ContinuityRevisionConflict as exc:
            raise HTTPException(status_code=409, detail="Continuity contract revision is stale") from exc
    elif (
        active is not None
        and active.revision == request.contract_revision + 1
        and active.model_copy(update={"revision": 0}) == candidate_semantics
    ):
        replay_observed = H3ObservedBoundary(
            value=request.observed_carry_out,
            source_contract_revision=request.contract_revision,
            result_contract_revision=active.revision,
            accepted=request.accept_deviation,
            deviation_reason=reason,
            lock_violations=request.lock_violations,
        )
        if (
            entry.observed_carry_out is not None
            and entry.observed_carry_out != replay_observed
        ):
            raise HTTPException(
                status_code=409, detail="Observed boundary replay does not match manifest"
            )
        try:
            saved = continuity_store.put(
                episode, candidate, expected_revision=request.contract_revision + 1
            )
        except ContinuityRevisionConflict as exc:
            raise HTTPException(
                status_code=409, detail="Continuity contract revision is stale"
            ) from exc
    else:
        raise HTTPException(status_code=409, detail="Continuity contract revision is stale")

    observed = H3ObservedBoundary(
        value=request.observed_carry_out,
        source_contract_revision=request.contract_revision,
        result_contract_revision=saved.revision,
        accepted=request.accept_deviation,
        deviation_reason=reason,
        lock_violations=request.lock_violations,
    )
    entries = list(manifest.entries)
    entries[entry_index] = entry.model_copy(update={"observed_carry_out": observed})
    if entry.observed_carry_out == observed:
        updated_manifest = manifest
    else:
        updated_manifest = H3DirectorOutputManifest(
            **manifest.model_dump(exclude={"entries"}), entries=tuple(entries)
        )
        try:
            save_h3_director_manifest(manifest_path, updated_manifest)
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail="Continuity revision was saved but the video manifest update failed; retry is safe",
            ) from exc

    data = _serialize_prompt_review(
        project,
        root,
        updated_manifest.model_dump(mode="json"),
        stage,
    )
    data["stale_dependent_shot_ids"] = sorted(
        item.shot_id
        for item in continuity_store.stale_dependents(episode, contract.shot_id)
    )
    return {"ok": True, "data": data}


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
        selected_beats = generation_beats_for_group(
            resolved.project_dir, episode, group_id, selected_beats
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Narrative group not found") from exc
    requirements = _active_reference_requirements(
        resolved.project_dir, episode, group_id
    )
    if requirements:
        store = await make_sqlite_store_for_context(resolved.ctx)
        preview = await resolve_requirement_reference_preview(
            store, requirements, stage=stage_name
        )
        data = _serialize_requirement_reference_preview(
            project, resolved.project_dir, preview
        )
    else:
        preview = resolve_group_reference_preview(
            resolved.project_dir, selected_beats, stage=stage_name
        )
        data = _serialize_reference_preview(project, resolved.project_dir, preview)
    return {
        "ok": True,
        "data": data,
    }


@router.get(
    "/projects/{project}/episodes/{episode}/narrative-groups/"
    "{group_id}/{stage_name}/references/candidates"
)
async def list_reference_candidates(
    project: str,
    episode: int,
    group_id: str,
    stage_name: Literal["sketch", "render"],
    user: dict = Depends(get_api_user),
):
    resolved, groups, _ = await _resolve_groups(project, episode, user)
    if not any(item.id == group_id for item in groups):
        raise HTTPException(status_code=404, detail="Narrative group not found")
    store = await make_sqlite_store_for_context(resolved.ctx)
    candidates = await _project_reference_assets(store, resolved.project_dir)
    return {"ok": True, "data": [
        {
            "id": asset_id,
            "kind": kind,
            "label": label,
            "available": True,
            "thumbnail_url": _asset_url(project, resolved.project_dir, path),
        }
        for asset_id, asset in candidates.items()
        for kind, label, path in [
            (asset.asset_kind, asset.entity_id, asset.image_path)
        ]
    ]}


@router.post(
    "/projects/{project}/episodes/{episode}/narrative-groups/"
    "{group_id}/{stage_name}/references/upload",
    status_code=status.HTTP_201_CREATED,
)
async def upload_group_reference(
    project: str,
    episode: int,
    group_id: str,
    stage_name: Literal["sketch", "render"],
    file: UploadFile = File(...),
    persist: bool = Form(False),
    requirement_id: str = Form(""),
    asset_kind: str = Form(""),
    target_entity_id: str = Form(""),
    base_entity_id: str = Form(""),
    variant_id: str = Form(""),
    user: dict = Depends(get_api_user),
):
    resolved, groups, _ = await _resolve_groups(project, episode, user)
    if not any(item.id == group_id for item in groups):
        raise HTTPException(status_code=404, detail="Narrative group not found")
    persist_resolver = None
    if persist:
        store = await make_sqlite_store_for_context(resolved.ctx)
        target = await _reference_persistence_target(
            store,
            resolved.project_dir,
            requirement_id=requirement_id,
            asset_kind=asset_kind,
            target_entity_id=target_entity_id,
            base_entity_id=base_entity_id,
            variant_id=variant_id,
        )
        def persist_resolver(_project_dir: Path, _request: Any) -> Path:
            return target
    try:
        upload = save_reference_upload(
            resolved.project_dir,
            await file.read(),
            file.content_type or "",
            file.filename or "",
            persist=persist,
            requirement_id=requirement_id,
            asset_kind=asset_kind,
            target_entity_id=target_entity_id,
            base_entity_id=base_entity_id,
            variant_id=variant_id,
            persist_resolver=persist_resolver,
        )
    except InvalidReferenceUpload as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    data = asdict(upload)
    data.pop("image_path", None)
    data["url"] = (
        f"/api/v1/projects/{quote(project, safe='')}/episodes/{episode}/"
        f"narrative-groups/{quote(group_id, safe='')}/{stage_name}/"
        f"references/uploads/{upload.upload_id}"
    )
    return {"ok": True, "data": data}


@router.get(
    "/projects/{project}/episodes/{episode}/narrative-groups/"
    "{group_id}/{stage_name}/references/uploads/{upload_id}"
)
async def get_group_reference_upload(
    project: str,
    episode: int,
    group_id: str,
    stage_name: Literal["sketch", "render"],
    upload_id: str,
    user: dict = Depends(get_api_user),
):
    resolved, groups, _ = await _resolve_groups(project, episode, user)
    if not any(item.id == group_id for item in groups):
        raise HTTPException(status_code=404, detail="Narrative group not found")
    upload = load_reference_upload(resolved.project_dir, upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="Reference upload not found")
    return FileResponse(upload.image_path, media_type=upload.mime_type)


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
    reference_snapshot = None
    if not split_only:
        requirements = _active_reference_requirements(
            resolved.project_dir, episode, group_id
        )
        if request.reference_resolution is not None:
            if not requirements:
                raise HTTPException(
                    status_code=422,
                    detail="Reference resolution requires director asset requirements",
                )
            store = await make_sqlite_store_for_context(resolved.ctx)
            preview = await resolve_requirement_reference_preview(
                store, requirements, stage=stage
            )
            assets = await _project_reference_assets(store, resolved.project_dir)
            decisions = request.reference_resolution.decisions
            additional_upload_ids = request.reference_resolution.additional_upload_ids
            requested_upload_ids = [
                *(item.upload_id for item in decisions if item.upload_id),
                *additional_upload_ids,
            ]
            uploads = {}
            for upload_id in requested_upload_ids:
                upload = load_reference_upload(resolved.project_dir, upload_id)
                if upload is not None:
                    uploads[upload_id] = upload
            style_asset_id = request.reference_resolution.style_asset_id
            if style_asset_id and style_asset_id not in assets:
                raise HTTPException(status_code=422, detail="Unknown style asset ID")
            try:
                reference_snapshot = build_reference_snapshot(
                    ReferenceMatchPreview(
                        requirements=preview.requirements,
                        bindings=preview.bindings,
                        warnings=preview.warnings,
                    ),
                    [item.model_dump() for item in decisions],
                    project_dir=resolved.project_dir,
                    project_assets=assets,
                    uploads=uploads,
                    additional_asset_ids=request.reference_resolution.additional_asset_ids,
                    additional_upload_ids=additional_upload_ids,
                    style_reference=(
                        assets[style_asset_id].image_path
                        if style_asset_id in assets else None
                    ),
                    max_images=MAX_GROUP_IMAGE_REFERENCES,
                )
            except InvalidReferenceDecisions as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        else:
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
                    "reference_resolution",
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
    if reference_snapshot is not None:
        payload["reference_resolution"] = jsonable_encoder(asdict(reference_snapshot))
        payload.update({
            "provider_id": provider_id,
            "model": model,
            "image_size": image_size,
            "constraint_mode": constraint_mode,
            "source_sketch_revision": source_sketch_revision,
            "source_sketch_asset": source_sketch_asset,
        })
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
    provider_workflow_id: str | None = None
    reference_limit: int | None = None
    reference_snapshot_id: str | None = None
    resolved_references = ()
    reference_segments = ()
    if workflow.reference_policy.required:
        reference_revision = source_group.video_reference_settings.revision
        provider_workflow_id = str(workflow.provider_workflow_id or "").strip()
        reference_limit = workflow.reference_policy.max_images
        if not provider_workflow_id:
            raise HTTPException(
                status_code=422,
                detail="Video reference provider workflow is unavailable",
            )
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
            resolved_references = await resolve_saved_video_references(
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
            reference_segments = tuple(segments)
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
            expected_settings_revision=settings.revision,
            expected_reference_revision=reference_revision,
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
        "settings_revision": group.video_settings.revision,
    }
    if segment_id:
        payload["segment_id"] = segment_id
    if reference_revision is not None:
        try:
            frozen_frames = freeze_h3_reference_frames(
                reference_segments,
                project_root=resolved.project_dir,
            )
            persisted_snapshot = persist_h3_reference_input_snapshot(
                state_root=resolved.ctx.state_dir,
                references=resolved_references,
                frames=frozen_frames,
                reference_revision=group.video_reference_settings.revision,
                reference_limit=int(reference_limit),
                provider_workflow_id=str(provider_workflow_id),
            )
            reference_snapshot_id = persisted_snapshot.snapshot_id
        except (OSError, TypeError, ValueError) as exc:
            restore_video_reservation(resolved.project_dir, episode, reservation)
            raise HTTPException(
                status_code=422,
                detail="Video reference input snapshot is invalid",
            ) from exc
        except Exception:
            restore_video_reservation(resolved.project_dir, episode, reservation)
            raise
        payload.update({
            "reference_contract_version": 1,
            "reference_revision": group.video_reference_settings.revision,
            "provider_workflow_id": provider_workflow_id,
            "reference_limit": reference_limit,
            "reference_snapshot_id": reference_snapshot_id,
            "reference_snapshot_digest": persisted_snapshot.digest,
        })
    try:
        queued = await get_task_backend().enqueue_project_task(
            resolved.ctx, task_type="narrative_group_video", queue_kind="video",
            episode=episode, scope=scope, payload=payload,
        )
    except Exception as exc:
        if reference_snapshot_id is not None:
            try:
                retain_h3_reference_snapshot(
                    state_root=resolved.ctx.state_dir,
                    snapshot_id=reference_snapshot_id,
                )
            except (OSError, ValueError):
                pass
            ownership = _reference_enqueue_ownership(
                ctx=resolved.ctx,
                episode=episode,
                scope=scope,
                snapshot_id=reference_snapshot_id,
                snapshot_digest=persisted_snapshot.digest,
            )
            if ownership == "unowned":
                restore_video_reservation(
                    resolved.project_dir, episode, reservation
                )
        else:
            restore_video_reservation(resolved.project_dir, episode, reservation)
        raise HTTPException(status_code=503, detail="Narrative group video queue is unavailable") from exc
    if reference_snapshot_id is not None:
        try:
            bind_h3_reference_snapshot_owner(
                state_root=resolved.ctx.state_dir,
                snapshot_id=reference_snapshot_id,
                snapshot_digest=persisted_snapshot.digest,
                task_id=str(queued.task_state.task_id),
            )
        except Exception:
            try:
                retain_h3_reference_snapshot(
                    state_root=resolved.ctx.state_dir,
                    snapshot_id=reference_snapshot_id,
                )
            except (OSError, ValueError):
                pass
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
