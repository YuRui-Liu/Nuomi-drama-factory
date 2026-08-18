"""Narrative-group aggregate API.

The sidecar is the server-side source of truth. Clients never submit their own
cell mapping, which prevents refreshes and retries from silently reshuffling a
grid.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import make_sqlite_store_for_context, resolve_project_scope
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
    rebuild_groups,
    rollback_stage_revision,
    stage_history,
)
from novelvideo.ports import get_task_backend

router = APIRouter()


class NarrativeGroupGenerationRequest(BaseModel):
    use_style: bool = True
    selected_character_reference_ids: list[str] | None = None
    selected_scene_reference_ids: list[str] | None = None


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
        for state in item["stages"].values():
            state.pop("revision_history", None)
            state["grid_asset"] = _asset_url(
                project, project_dir, state.get("grid_asset", "")
            )
            for cell in state.get("cell_assets") or []:
                url = _asset_url(project, project_dir, str(cell.get("path") or ""))
                cell["path"] = url
                cell["url"] = url
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
        _, selected_beats = _group_beats(groups, beats, group_id)
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
        reference_selection = request.model_dump()
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
    if reference_selection is not None:
        payload["reference_selection"] = reference_selection
    queued = await get_task_backend().enqueue_project_task(
        resolved.ctx,
        task_type=task_type,
        queue_kind="default",
        episode=episode,
        scope=scope,
        payload=payload,
    )
    return {
        "ok": True,
        "data": {
            "task_id": queued.task_state.task_id,
            "scope": scope,
            "backend": queued.backend,
            "queue": queued.queue,
            "metadata": {
                "group_id": group_id,
                "stage": stage,
                "revision": revision,
            },
        },
    }


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
