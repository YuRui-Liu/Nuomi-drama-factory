"""Narrative-group aggregate API.

The sidecar is the server-side source of truth. Clients never submit their own
cell mapping, which prevents refreshes and retries from silently reshuffling a
grid.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import make_sqlite_store_for_context, resolve_project_scope
from novelvideo.narrative_groups.models import NarrativeGroup, StageName
from novelvideo.narrative_groups.service import (
    advance_revision,
    ensure_groups,
    rebuild_groups,
    rollback_stage_revision,
    stage_history,
    update_video_manifest_dialogue_source,
)
from novelvideo.ports import get_task_backend

router = APIRouter()


class NarrativeGroupVideoRequest(BaseModel):
    """Client input deliberately excludes mutable frame and beat payloads."""

    model_config = ConfigDict(extra="forbid")
    model: str = Field(default="minimax-h3", min_length=1)
    mode: Literal["auto", "i2va", "fl2va"] = "auto"
    revision: int = Field(ge=0)


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


@router.get("/projects/{project}/episodes/{episode}/narrative-groups")
async def list_narrative_groups(
    project: str,
    episode: int,
    user: dict = Depends(get_api_user),
):
    resolved, groups, _ = await _resolve_groups(project, episode, user)
    return {"ok": True, "data": _serialize(project, resolved.project_dir, groups)}


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
):
    resolved, _, beats = await _resolve_groups(project, episode, user)
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
        "beat_ids": list(group.beat_ids),
        "cell_to_beat": mapping,
        "beats": [beat_by_id[beat_id] for beat_id in group.beat_ids if beat_id in beat_by_id],
        "split_only": split_only,
    }
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
):
    resolved, _, _ = await _resolve_groups(project, episode, user)
    try:
        group, revision = advance_revision(
            resolved.project_dir, episode, group_id, "video",
            regenerate=True,
            expected_revision=request.revision,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Narrative group '{group_id}' not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail="Narrative group video revision is stale")
    scope = f"group_{group_id}_video_r{revision}"
    payload = {
        "episode": episode,
        "group_id": group.id,
        "revision": revision,
        "model": request.model,
        "mode": request.mode,
    }
    queued = await get_task_backend().enqueue_project_task(
        resolved.ctx, task_type="narrative_group_video", queue_kind="default",
        episode=episode, scope=scope, payload=payload,
    )
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
    project: str, episode: int, group_id: str, user: dict = Depends(get_api_user)
):
    return await _enqueue_group_action(project, episode, group_id, "sketch", user)


@router.post(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/render/generate",
    status_code=status.HTTP_202_ACCEPTED,
)
@router.post(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/render-grid/generate",
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_render_group(
    project: str, episode: int, group_id: str, user: dict = Depends(get_api_user)
):
    return await _enqueue_group_action(project, episode, group_id, "render", user)


@router.post(
    "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/video/generate",
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_video_group(
    project: str,
    episode: int,
    group_id: str,
    request: NarrativeGroupVideoRequest = Body(default_factory=NarrativeGroupVideoRequest),
    user: dict = Depends(get_api_user),
):
    return await _enqueue_group_video(project, episode, group_id, user, request)


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
    user: dict = Depends(get_api_user),
):
    return await _enqueue_group_action(
        project, episode, group_id, stage_name, user, split_only=True
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
    user: dict = Depends(get_api_user),
):
    return await _enqueue_group_action(
        project, episode, group_id, stage_name, user, regenerate=True
    )
