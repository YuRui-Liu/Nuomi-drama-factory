"""Narrative-group aggregate API.

The sidecar is the server-side source of truth. Clients never submit their own
cell mapping, which prevents refreshes and retries from silently reshuffling a
grid.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Mapping
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import (
    get_media_capability_store,
    get_media_credential_resolver,
    make_sqlite_store_for_context,
    resolve_project_scope,
)
from novelvideo.media_capabilities.models import GRSAI_IMAGE_MODELS
from novelvideo.media_capabilities.runtime.credentials import CredentialResolver
from novelvideo.media_capabilities.store import MediaCapabilityStore
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
    ensure_groups,
    load_group_video_prompt_manifest,
    rebuild_groups,
    rollback_stage_revision,
    reserve_video_revision,
    restore_video_reservation,
    stage_history,
    update_video_manifest_dialogue_source,
    update_video_plan,
)
from novelvideo.ports import get_task_backend

router = APIRouter()


class NarrativeGroupGenerationRequest(BaseModel):
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
    use_style: bool = True
    selected_character_reference_ids: list[str] | None = None
    selected_scene_reference_ids: list[str] | None = None
    provider_id: str | None = None
    model: str | None = None
    allow_unconstrained: bool = False


def _image_binding(
    ctx, project_dir: Path, stage: StageName, request: NarrativeGroupGenerationRequest
) -> tuple[str, str]:
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
    return provider_id, model


class NarrativeGroupVideoRequest(BaseModel):
    """Client input deliberately excludes mutable frame and beat payloads."""

    model_config = ConfigDict(extra="forbid")
    model: str = Field(default="runninghub:minimax-h3", min_length=1)
    mode: Literal["auto", "i2va", "fl2va"] = "auto"
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
    resolution: str | None = None
    revision: int = Field(ge=0)
    plan_revision: int = Field(ge=1)


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


async def _resolve_groups(project: str, episode: int, user: dict, *, rebuild: bool = False):
    resolved = await resolve_project_scope(project, user, required_role="editor")
    store = await make_sqlite_store_for_context(resolved.ctx)
    beats = await store.get_beats_as_dicts(episode)
    groups = (
        rebuild_groups(resolved.project_dir, episode, beats)
        if rebuild
        else ensure_groups(resolved.project_dir, episode, beats)
    )
    return resolved, groups, beats


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


def _serialize(project: str, project_dir: Path, groups: list[NarrativeGroup]) -> list[dict]:
    result = []
    for group in groups:
        item = group.to_dict()
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


_PROMPT_REVIEW_SECRET_KEYS = (
    "api_key", "authorization", "credential", "secret", "workflow_json",
)
_SAFE_INPUT_SUMMARY_KEYS = {
    "beat_ids", "mode", "duration_seconds", "aspect_ratio", "resolution",
    "first_frame_sha256", "last_frame_sha256",
}


def _manifest_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    return dict(dump(mode="json")) if callable(dump) else {}


def _safe_prompt_review_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        result = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            lowered = key.lower()
            if any(secret in lowered for secret in _PROMPT_REVIEW_SECRET_KEYS):
                continue
            if lowered.endswith("_path") or lowered.endswith("_paths"):
                continue
            sanitized = _safe_prompt_review_value(item)
            if sanitized is not None:
                result[key] = sanitized
        return result
    if isinstance(value, (list, tuple)):
        return [
            sanitized
            for item in value
            if (sanitized := _safe_prompt_review_value(item)) is not None
        ]
    if isinstance(value, str) and Path(value).is_absolute():
        return None
    return value


def _review_beat_ids(entry: Mapping[str, Any], segment: Mapping[str, Any]) -> list[str]:
    summary = _manifest_mapping(entry.get("input_summary"))
    beat_ids = [str(value) for value in summary.get("beat_ids") or () if str(value)]
    if beat_ids:
        return beat_ids
    segment_id = str(segment.get("segment_id") or "").strip()
    if segment_id:
        return [part for part in segment_id.split("--") if part]
    beat_number = segment.get("beat_number")
    return [f"beat-{beat_number}"] if beat_number is not None else []


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
        summary = _manifest_mapping(entry.get("input_summary"))
        plan = _manifest_mapping(entry.get("director_plan"))
        beat_ids = _review_beat_ids(entry, segment)
        first_frame = str(segment.get("first_frame") or "")
        last_frame = str(segment.get("last_frame") or "")
        mode = str(
            summary.get("mode")
            or plan.get("mode")
            or getattr(stage, "actual_mode", "")
            or ("fl2va" if last_frame else "i2va")
        )
        duration = (
            summary.get("duration_seconds")
            or entry.get("actual_duration_seconds")
            or segment.get("duration_seconds")
            or 0
        )
        safe_summary = {
            key: value
            for key, value in summary.items()
            if key in _SAFE_INPUT_SUMMARY_KEYS
        }
        units.append({
            "beat_ids": beat_ids,
            "label": _review_label(beat_ids),
            "mode": mode,
            "duration_seconds": duration,
            "first_frame_url": _asset_url(project, project_dir, first_frame),
            "last_frame_url": _asset_url(project, project_dir, last_frame),
            "director_plan": (
                _safe_prompt_review_value(plan) if entry.get("director_plan") is not None else None
            ),
            "final_prompt": str(segment.get("prompt") or ""),
            "prompt_profile": (
                _safe_prompt_review_value(_manifest_mapping(entry.get("prompt_profile")))
                if entry.get("prompt_profile") is not None else None
            ),
            "quality_report": (
                _safe_prompt_review_value(_manifest_mapping(entry.get("quality_report")))
                if entry.get("quality_report") is not None else None
            ),
            "input_summary": _safe_prompt_review_value(safe_summary),
            "workflow": str(entry.get("workflow_id") or manifest.get("workflow_id") or ""),
            "model": str(entry.get("model") or manifest.get("model") or getattr(stage, "actual_model", "")),
            "provider": str(getattr(stage, "actual_provider", "") or ""),
            "provider_task_id": str(
                entry.get("provider_task_id") or manifest.get("provider_task_id") or ""
            ),
        })
    return {"units": units}


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
            exclude={"aspect_ratio", "provider_id", "model", "allow_unconstrained"}
        )
    provider_id = model = ""
    constraint_mode = ""
    source_sketch_revision = 0
    source_sketch_asset = ""
    if not split_only:
        provider_id, model = _image_binding(
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
    beat_by_id = {
        str(beat.get("id") or beat.get("beat_id") or beat.get("beat_number")): beat
        for beat in beats
    }
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
        "beats": [beat_by_id[beat_id] for beat_id in group.beat_ids if beat_id in beat_by_id],
        "split_only": split_only,
    }
    if reference_selection is not None:
        payload["reference_selection"] = reference_selection
        payload.update(
            {
                "provider_id": provider_id,
                "model": model,
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
    resolved, _, _ = await _resolve_groups(project, episode, user)
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
        "resolution": request.resolution,
    }
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
        group = update_video_plan(
            resolved.project_dir,
            episode,
            group_id,
            beats,
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
