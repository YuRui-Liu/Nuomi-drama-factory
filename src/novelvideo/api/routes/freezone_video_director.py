"""Project-scoped canvas video Director API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import get_media_capability_store
from novelvideo.api.routes.freezone import _resolve_freezone_project
from novelvideo.freezone.video_director.capabilities import (
    DirectorCapabilityError, describe_capabilities,
)
from novelvideo.freezone.video_director.models import DirectorDraft
from novelvideo.freezone.video_director.service import DirectorService
from novelvideo.ports import get_task_backend


router = APIRouter(prefix="/projects/{project}/freezone/video-director", tags=["freezone-video"])


class CreateAttemptBody(BaseModel):
    canvas_id: str = Field(min_length=1, max_length=128)
    node_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    draft: DirectorDraft


def _limits():
    configured = get_media_capability_store().get_runninghub_workflows().video_minimax_h3_ref_max_images
    return configured, min(configured, 8)


def _public(item):
    result = {key: item.get(key) for key in (
        "id", "project_id", "canvas_id", "node_id", "request_id", "parent_attempt_id",
        "snapshot", "stage", "optimized", "rules_hash", "reference_limit", "workflow_id",
        "workflow_profile_id", "workflow_profile_version", "actual_parameters",
        "task_id", "provider_task_id", "result_url", "error", "failed_stage",
        "created_at", "updated_at",
    )}
    result["revision"] = item["snapshot"]["revision"]
    return result


async def _service(project: str, user: dict, *, role: str):
    ctx, *_ = await _resolve_freezone_project(project, user, required_role=role)
    _, effective = _limits()
    return ctx, DirectorService(ctx, reference_limit=effective)


async def _enqueue(ctx, service: DirectorService, item):
    if item.get("task_id") or item["stage"] in {"completed", "submission_unknown"}:
        return item
    queued = await get_task_backend().enqueue_project_task(
        ctx, task_type="freezone_video_director", queue_kind="default", scope=item["id"],
        payload={"attempt_id": item["id"], "canvas_id": item["canvas_id"],
                 "node_id": item["node_id"]},
    )
    return service.store.update(item["id"], task_id=queued.task_state.task_id)


@router.get("/capabilities")
async def capabilities(project: str, user: dict = Depends(get_api_user)):
    await _resolve_freezone_project(project, user, required_role="viewer")
    configured, effective = _limits()
    data = describe_capabilities(effective)
    data.update(configured_reference_limit=configured, effective_reference_limit=effective,
                frame_step=17, frame_offset=5)
    return {"ok": True, "data": data}


@router.post("/attempts", status_code=status.HTTP_202_ACCEPTED)
async def create_attempt(project: str, body: CreateAttemptBody,
                         user: dict = Depends(get_api_user)):
    ctx, service = await _service(project, user, role="editor")
    try:
        item, _ = service.create(body.canvas_id, body.node_id, body.request_id, body.draft)
    except DirectorCapabilityError as exc:
        raise HTTPException(422, detail={"field": exc.field, "segment_id": exc.segment_id,
                                         "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(422, detail={"field": "draft", "message": str(exc)}) from exc
    item = await _enqueue(ctx, service, item)
    return {"ok": True, "data": {"attempt_id": item["id"], "task_id": item.get("task_id"),
                                  "attempt": _public(item)}}


@router.get("/attempts")
async def list_attempts(project: str, canvas_id: str | None = Query(default=None),
                        node_id: str | None = Query(default=None),
                        user: dict = Depends(get_api_user)):
    _, service = await _service(project, user, role="viewer")
    return {"ok": True, "data": {"attempts": [_public(item) for item in service.list(canvas_id, node_id)]}}


@router.get("/attempts/{attempt_id}")
async def get_attempt(project: str, attempt_id: str, user: dict = Depends(get_api_user)):
    _, service = await _service(project, user, role="viewer")
    try:
        return {"ok": True, "data": {"attempt": _public(service.get(attempt_id))}}
    except KeyError as exc:
        raise HTTPException(404, detail="Director attempt not found") from exc


@router.post("/attempts/{attempt_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_attempt(project: str, attempt_id: str, user: dict = Depends(get_api_user)):
    ctx, service = await _service(project, user, role="editor")
    try:
        item, _ = service.retry(attempt_id)
    except KeyError as exc:
        raise HTTPException(404, detail="Director attempt not found") from exc
    except ValueError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    item = await _enqueue(ctx, service, item)
    return {"ok": True, "data": {"attempt_id": item["id"], "task_id": item.get("task_id"),
                                  "attempt": _public(item)}}
