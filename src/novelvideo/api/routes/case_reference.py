from fastapi import APIRouter, Depends, HTTPException

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes.freezone_video_director import _limits
from novelvideo.api.routes.freezone import _resolve_freezone_project
from novelvideo.api.routes.technique_library import _owner
from novelvideo.case_reference.models import ProposalInput
from novelvideo.case_reference.service import ProposalService
from novelvideo.ports import get_task_backend
from novelvideo.task_state import get_task_manager
from novelvideo.technique_library.catalog import CatalogUnavailable

router = APIRouter(prefix='/projects/{project}/freezone/case-reference', tags=['case-reference'])


async def _service(project, user, role):
    ctx, *_ = await _resolve_freezone_project(project, user, required_role=role)
    return ctx, ProposalService(ctx, reference_limit=_limits()[1]), _owner(user)


@router.post('/proposals', status_code=202)
async def create(project: str, body: ProposalInput, user: dict = Depends(get_api_user)):
    ctx, service, owner = await _service(project, user, 'editor')
    try:
        item, created = service.create(owner, body)
    except CatalogUnavailable as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409 if 'request_id' in str(exc) else 422, detail=str(exc)) from exc
    if created:
        try:
            task = await get_task_backend().enqueue_project_task(ctx, task_type='case_reference', queue_kind='default',
                scope=item['id'], payload={'proposal_id': item['id'], 'owner': owner,
                    'canvas_id': body.canvas_id, 'node_id': body.node_id})
            item = service.store.transition(item['id'], owner, ('queued', 'running', 'succeeded', 'failed', 'cancelled'),
                None, task_id=task.task_state.task_id)
        except Exception:
            service.store.transition(item['id'], owner, ('queued',), 'failed', error='任务提交失败，请重试')
            raise HTTPException(503, detail='参考方案任务提交失败') from None
    return {'ok': True, 'data': item}


@router.get('/proposals')
async def list_proposals(project: str, canvas_id: str, node_id: str, segment_id: str,
                         user: dict = Depends(get_api_user)):
    _, service, owner = await _service(project, user, 'viewer')
    return {'ok': True, 'data': service.store.list(owner, canvas_id, node_id, segment_id)}


@router.get('/proposals/{proposal_id}')
async def get_proposal(project: str, proposal_id: str, user: dict = Depends(get_api_user)):
    _, service, owner = await _service(project, user, 'viewer')
    try:
        return {'ok': True, 'data': service.store.get(proposal_id, owner)}
    except KeyError:
        raise HTTPException(404, detail='参考方案不存在') from None


@router.post('/proposals/{proposal_id}/cancel')
async def cancel(project: str, proposal_id: str, user: dict = Depends(get_api_user)):
    ctx, service, owner = await _service(project, user, 'editor')
    try:
        item = service.store.transition(proposal_id, owner, ('queued', 'running', 'succeeded'), 'cancelled', result=None)
        task = get_task_manager().get_task_for_project(ctx, 'case_reference', 0, scope=proposal_id)
        if task is not None:
            await get_task_backend().cancel_project_task(ctx, task)
        return {'ok': True, 'data': item}
    except KeyError:
        raise HTTPException(404, detail='参考方案不存在') from None
