"""Project-authorized creation operations; legacy projects remain untouched."""

from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from novelvideo.api.auth import get_api_user
from novelvideo.creation.models import OperationRequest
from novelvideo.creation.operations import OperationError, OperationService
from novelvideo.creation.store import ObjectStore
from novelvideo.project_config import load_project_config_file_from_state_dir
from novelvideo.project_context import require_project_home_node, resolve_project_context

router = APIRouter(prefix='/projects/{project}/creation', tags=['creation'])


@contextmanager
def errors():
    try:
        yield
    except OperationError as exc:
        status = 403 if exc.code == 'scope_violation' else 503 if exc.code == 'capability_unavailable' else 422 if exc.code == 'invalid_contract' else 409
        raise HTTPException(status, exc.as_dict()) from exc
    except (ValidationError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(422, {'code': 'invalid_contract', 'message': '操作字段不符合约定'}) from exc


async def service_for(project, user, role):
    try:
        ctx = await resolve_project_context(user=user, project_id=project, required_role=role)
    except HTTPException as exc:
        if exc.status_code in {403, 404}:
            raise HTTPException(403, {'code': 'scope_violation', 'message': '无权访问此项目', 'project_id': project}) from exc
        raise
    require_project_home_node(ctx, operation='创作画布访问')
    if load_project_config_file_from_state_dir(ctx.state_dir).get('creation_mode') != 'agent_canvas_v1':
        raise OperationError('legacy_project_readonly', '旧项目暂不支持创作画布操作', project_id=ctx.project_id)
    actor = ctx.requester_user_id
    return actor, OperationService(ObjectStore(ctx.state_dir), project_id=ctx.project_id,
                                   authorize=lambda candidate, target: candidate == actor and target == ctx.project_id)


@router.post('/operations')
async def apply_operation(project: str, body: dict, user: dict = Depends(get_api_user)):
    with errors():
        actor, service = await service_for(project, user, 'editor')
        result = service.apply(actor, OperationRequest.model_validate(body))
        return {'ok': True, 'data': result.model_dump()}


@router.get('/operations/{operation_id}')
async def get_operation(project: str, operation_id: str, user: dict = Depends(get_api_user)):
    with errors():
        actor, service = await service_for(project, user, 'viewer')
        return {'ok': True, 'data': service.get(actor, operation_id).model_dump()}


@router.get('/assets/{base_id}/uses')
async def asset_uses(project: str, base_id: str, user: dict = Depends(get_api_user)):
    with errors():
        actor, service = await service_for(project, user, 'viewer')
        return {'ok': True, 'data': service.asset_uses(actor, base_id)}
