"""Project-scoped draft asset design and image-generation preparation API."""

from fastapi import APIRouter, Depends

from novelvideo.api.auth import get_api_user
from novelvideo.creation.asset_adapter import registration_io
from novelvideo.creation.asset_generation import AssetDesignPrepare, AssetGeneration
from novelvideo.production_workflow.coordination import asset_project_lock
from .creation import context_for, errors


router = APIRouter(prefix='/projects/{project}/creation', tags=['creation-assets'])


@router.post('/asset-designs/prepare')
async def prepare_asset_design(project: str, body: dict, user: dict = Depends(get_api_user)):
    with errors():
        request = AssetDesignPrepare.model_validate(body)
        ctx = await context_for(project, user, 'editor')
        service = AssetGeneration(ctx)
        async with asset_project_lock(ctx.state_dir):
            result = await registration_io(service.prepare, ctx.requester_user_id, request)
        return {'ok': True, 'data': result}


@router.get('/asset-design-requests/{request_id}')
async def asset_design_request_status(project: str, request_id: str,
                                      user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project, user, 'editor')
        service = AssetGeneration(ctx)
        async with asset_project_lock(ctx.state_dir):
            result = await registration_io(service.status, request_id)
        return {'ok': True, 'data': result}


@router.get('/asset-designs/{design_id}')
async def read_asset_design(project: str, design_id: str, revision: int | None = None,
                            user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project, user, 'viewer')
        service = AssetGeneration(ctx)
        result = await registration_io(service.get_design, design_id, revision)
        return {'ok': True, 'data': result}
