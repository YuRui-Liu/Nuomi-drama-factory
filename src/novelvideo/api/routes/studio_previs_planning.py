"""Explicit, idempotent paid-text planning for editable previs suggestions."""
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam
from pydantic import BaseModel, ConfigDict, Field

from novelvideo.api.auth import get_api_user, require_scope
from novelvideo.api.routes.director_plans import _resolve
from novelvideo.creative_studios.previs import PrevisScene
from novelvideo.creative_studios.previs_planning import claim_plan
from novelvideo.creative_studios.store import StudioStore

router = APIRouter(prefix='/projects/{project}/studios/previs-tools/plans')
RequestId = Annotated[str, PathParam(pattern=r'^[a-f0-9-]{32,36}$')]


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scene: PrevisScene
    instruction: str = Field(min_length=1, max_length=12000)


async def _status(ctx, request_id):
    from novelvideo.task_state import get_task_manager
    store = StudioStore(Path(ctx.state_dir) / 'creative-studios.db')
    document = await asyncio.to_thread(store.get, 'previs', f'previs-plan-{request_id}')
    task = get_task_manager().get_task_for_project(ctx, 'studio_previs_plan', 0, scope=f'previs:{request_id}')
    return {'request_id': request_id, 'status': 'completed' if document else str(task.status) if task else 'unknown', 'task_id': task.task_id if task else None, 'error': task.error if task else None, 'document': document}


@router.get('/{request_id}')
async def plan_status(project: str, request_id: RequestId, user: dict = Depends(get_api_user)):
    return {'ok': True, 'data': await _status(await _resolve(project, user, role='viewer'), request_id)}


@router.post('/{request_id}')
async def submit_plan(project: str, request_id: RequestId, body: PlanRequest, user: dict = Depends(require_scope('tasks:submit'))):
    from novelvideo.ports import get_task_backend
    ctx = await _resolve(project, user, role='editor')
    payload = body.model_dump(mode='json', exclude_none=True)
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    try:
        claimed = await asyncio.to_thread(claim_plan, Path(ctx.state_dir) / 'creative-studios.db', request_id, fingerprint)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not claimed:
        return {'ok': True, 'data': {**await _status(ctx, request_id), 'reused': True}}
    await get_task_backend().enqueue_project_task(ctx, task_type='studio_previs_plan', queue_kind='default', episode=0, scope=f'previs:{request_id}', payload={**payload, 'project_id': str(ctx.project_id), 'result_document_id': f'previs-plan-{request_id}', 'display_name': '自然语言预演规划'})
    return {'ok': True, 'data': {**await _status(ctx, request_id), 'reused': False}}
