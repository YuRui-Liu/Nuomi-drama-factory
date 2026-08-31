"""Versioned screenplay semantic extraction and review endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from novelvideo.api.auth import get_api_user, require_scope
from novelvideo.api.deps import make_sqlite_store_for_context, resolve_project_scope
from novelvideo.episode_source_store import EpisodeSourceStore
from novelvideo.ports import get_task_backend
from novelvideo.screenplay_semantics import ScreenplaySemanticActivationConflict, ScreenplaySemanticStore
from novelvideo.task_identity import project_task_state_key

router = APIRouter()


class CreateSemanticRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene_ids: list[str] = Field(default_factory=list)
    concurrency: int = Field(default=5, ge=1, le=20)


async def _resolve(project: str, user: dict, *, role: str):
    resolved = await resolve_project_scope(project, user, required_role=role)
    if resolved.ctx is None:
        raise HTTPException(409, detail={"code": "project_context_required"})
    return resolved.ctx


async def _resolve_source_revision(ctx, episode: int) -> int:
    repository = EpisodeSourceStore(await make_sqlite_store_for_context(ctx))
    source = next((item for item in await repository.list_sources() if item.episode_number == episode), None)
    if source is None:
        raise HTTPException(404, detail={"code": "EPISODE_SOURCE_NOT_FOUND"})
    return int(source.source_revision)


def _store(ctx) -> ScreenplaySemanticStore:
    return ScreenplaySemanticStore(ctx.output_dir)


@router.post("/projects/{project}/episodes/{episode}/screenplay-semantics", status_code=status.HTTP_202_ACCEPTED)
async def create_screenplay_semantics(
    project: str, episode: int, body: CreateSemanticRequest,
    user: dict = Depends(require_scope("tasks:submit")),
):
    ctx = await _resolve(project, user, role="editor")
    source_revision = await _resolve_source_revision(ctx, episode)
    scope = f"revision:{source_revision}"
    queued = await get_task_backend().enqueue_project_task(
        ctx, task_type="screenplay_semantics", queue_kind="default", episode=episode, scope=scope,
        payload={"project_id": str(ctx.project_id), "episode": episode,
                 "source_revision": source_revision, "scene_ids": body.scene_ids,
                 "concurrency": body.concurrency},
    )
    return {"ok": True, "task_type": "screenplay_semantics",
            "task_id": queued.task_state.task_id,
            "task_key": project_task_state_key("screenplay_semantics", str(ctx.project_id), episode, scope=scope),
            "backend": queued.backend, "queue": queued.queue,
            "source_revision": source_revision}


@router.get("/projects/{project}/episodes/{episode}/screenplay-semantics")
async def list_screenplay_semantics(project: str, episode: int, user: dict = Depends(get_api_user)):
    ctx = await _resolve(project, user, role="viewer")
    store = _store(ctx)
    active = store.load_active(episode)
    return {"ok": True, "data": {"active_revision_id": active.revision_id if active else None,
        "revisions": [item.model_dump(mode="json") for item in store.list_revisions(episode)]}}


@router.get("/projects/{project}/episodes/{episode}/screenplay-semantics/{revision_id}")
async def get_screenplay_semantics(project: str, episode: int, revision_id: str, user: dict = Depends(get_api_user)):
    ctx = await _resolve(project, user, role="viewer")
    revision = _store(ctx).load(episode, revision_id)
    if revision is None:
        raise HTTPException(404, detail={"code": "SCREENPLAY_SEMANTIC_REVISION_NOT_FOUND"})
    return {"ok": True, "data": revision.model_dump(mode="json")}


@router.post("/projects/{project}/episodes/{episode}/screenplay-semantics/{revision_id}/activate")
async def activate_screenplay_semantics(project: str, episode: int, revision_id: str,
    user: dict = Depends(require_scope("tasks:submit"))):
    ctx = await _resolve(project, user, role="editor")
    current_revision = await _resolve_source_revision(ctx, episode)
    try:
        revision = _store(ctx).activate(episode, revision_id, expected_source_revision=current_revision)
    except LookupError as exc:
        raise HTTPException(404, detail={"code": "SCREENPLAY_SEMANTIC_REVISION_NOT_FOUND"}) from exc
    except ScreenplaySemanticActivationConflict as exc:
        raise HTTPException(409, detail={"code": "SCREENPLAY_SEMANTIC_ACTIVATION_CONFLICT", "error": str(exc)}) from exc
    return {"ok": True, "data": revision.model_dump(mode="json")}


@router.post("/projects/{project}/episodes/{episode}/screenplay-semantics/{revision_id}/scenes/{scene_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_screenplay_semantic_scene(project: str, episode: int, revision_id: str, scene_id: str,
    user: dict = Depends(require_scope("tasks:submit"))):
    ctx = await _resolve(project, user, role="editor")
    revision = _store(ctx).load(episode, revision_id)
    if revision is None or scene_id not in {item.id for item in revision.scenes}:
        raise HTTPException(404, detail={"code": "SCREENPLAY_SEMANTIC_SCENE_NOT_FOUND"})
    source_revision = await _resolve_source_revision(ctx, episode)
    if source_revision != revision.source_revision:
        raise HTTPException(409, detail={"code": "SOURCE_REVISION_CONFLICT"})
    scope = f"revision:{source_revision}:scene:{scene_id}"
    queued = await get_task_backend().enqueue_project_task(
        ctx, task_type="screenplay_semantics", queue_kind="default", episode=episode, scope=scope,
        payload={"project_id": str(ctx.project_id), "episode": episode,
                 "source_revision": source_revision, "scene_ids": [scene_id], "concurrency": 1},
    )
    return {"ok": True, "task_type": "screenplay_semantics", "task_id": queued.task_state.task_id,
            "task_key": project_task_state_key("screenplay_semantics", str(ctx.project_id), episode, scope=scope),
            "backend": queued.backend, "queue": queued.queue, "source_revision": source_revision}


__all__ = ["CreateSemanticRequest", "router"]
