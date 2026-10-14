"""Project-authorized creation operations; legacy projects remain untouched."""

from contextlib import closing, contextmanager
import asyncio
import json
import logging
from pathlib import Path
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from novelvideo.api.auth import get_api_user
from novelvideo.creation.models import OperationRequest
from novelvideo.creation.canvas_workspaces import CanvasCreate, CanvasWorkspace, canvas_response, list_canvases
from novelvideo.creation.operations import OperationError, OperationService
from novelvideo.creation.projection import ProjectionService
from novelvideo.creation.store import ObjectStore
from novelvideo.creation.asset_reference import AssetReferences, AssetReferenceError, readonly, table
from novelvideo.creation.asset_sync import SourceSyncPending
from novelvideo.creation.project_mode import is_canvas_project, require_canvas_project
from novelvideo.creation.workspace import EpisodeCreate, EpisodeWorkspace, episode_response, list_episodes
from novelvideo.project_config import load_project_config_file_from_state_dir
from novelvideo.project_context import require_project_home_node, resolve_project_context

router = APIRouter(prefix='/projects/{project}/creation', tags=['creation'])
logger = logging.getLogger(__name__)


async def recover_pending_projections(state_root: Path) -> None:
    """Recover existing new-mode stores only, rechecking server-side access."""
    for db_path in state_root.rglob('creation/objects.sqlite3'):
        state_dir = db_path.parent.parent
        try:
            if not is_canvas_project(state_dir, config=load_project_config_file_from_state_dir(state_dir)):
                continue
            with closing(sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True)) as conn:
                conn.row_factory = sqlite3.Row
                pending = conn.execute("SELECT id, actor_id, payload FROM operations WHERE state='pending_projection' ORDER BY rowid").fetchall()
        except Exception:
            logger.exception('Cannot inspect creation recovery store %s', db_path)
            continue
        for row in pending:
            try:
                project_id = json.loads(row['payload'])['scope']['project_id']
                ctx = await resolve_project_context(user={'id': row['actor_id']}, project_id=project_id, required_role='editor')
                require_project_home_node(ctx, operation='创作画布恢复')
                if (Path(ctx.state_dir).resolve() != state_dir.resolve()
                        or ctx.project_id != project_id or ctx.requester_user_id != row['actor_id']):
                    raise ValueError('Recovery project scope does not match stored operation')
                if not is_canvas_project(ctx.state_dir, config=load_project_config_file_from_state_dir(ctx.state_dir)):
                    continue
                await asyncio.to_thread(ProjectionService(ObjectStore(ctx.state_dir)).apply, row['id'])
            except Exception:
                logger.exception('Creation projection recovery failed for operation %s in %s', row['id'], db_path)


@contextmanager
def errors():
    try:
        yield
    except OperationError as exc:
        status = 403 if exc.code == 'scope_violation' else 503 if exc.code == 'capability_unavailable' else 422 if exc.code == 'invalid_contract' else 409
        raise HTTPException(status, exc.as_dict()) from exc
    except AssetReferenceError as exc:
        raise HTTPException(409, {'code': exc.code, 'message': str(exc)}) from exc
    except SourceSyncPending as exc:
        raise HTTPException(409, {'code': 'asset_media_pending', 'message': str(exc)}) from exc
    except (ValidationError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(422, {'code': 'invalid_contract', 'message': '操作字段不符合约定'}) from exc


async def context_for(project, user, role):
    try:
        ctx = await resolve_project_context(user=user, project_id=project, required_role=role)
    except HTTPException as exc:
        if exc.status_code in {403, 404}:
            raise HTTPException(403, {'code': 'scope_violation', 'message': '无权访问此项目', 'project_id': project}) from exc
        raise
    require_project_home_node(ctx, operation='创作画布访问')
    require_canvas_project(ctx.state_dir, ctx.project_id,
                           config=load_project_config_file_from_state_dir(ctx.state_dir))
    return ctx


async def service_for(project, user, role):
    ctx = await context_for(project, user, role)
    actor = ctx.requester_user_id
    return actor, OperationService(ObjectStore(ctx.state_dir), project_id=ctx.project_id,
                                   output_dir=ctx.output_dir,
                                   authorize=lambda candidate, target: candidate == actor and target == ctx.project_id)


@router.get('/assets')
async def browse_assets(project: str, user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project, user, 'viewer')
        service = AssetReferences(ctx.state_dir, ctx.output_dir, ctx.project_id)
        return {'ok': True, 'data': await asyncio.to_thread(service.catalog)}


@router.get('/asset-publications/source')
async def inspect_publication_source(project: str, canvas_id: str, node_id: str, user: dict = Depends(get_api_user)):
    from novelvideo.creation.asset_publication import SharedAssetPublication
    with errors():
        ctx = await context_for(project, user, 'viewer')
        return {'ok': True, 'data': await asyncio.to_thread(SharedAssetPublication(ctx).inspect_source, canvas_id, node_id)}


@router.post('/asset-publications')
async def publish_shared_asset(project: str, body: dict, user: dict = Depends(get_api_user)):
    from novelvideo.creation.asset_publication import SharedAssetPublication
    with errors():
        ctx = await context_for(project, user, 'editor')
        return {'ok': True, 'data': await SharedAssetPublication(ctx).publish(body)}


@router.get('/asset-publications/{request_id}')
async def shared_asset_publication_status(project: str, request_id: str, user: dict = Depends(get_api_user)):
    from novelvideo.creation.asset_publication import SharedAssetPublication
    with errors():
        ctx = await context_for(project, user, 'viewer')
        return {'ok': True, 'data': await asyncio.to_thread(SharedAssetPublication(ctx).status, request_id)}


@router.post('/assets/register')
async def register_asset(project: str, body: dict, user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project, user, 'editor')
        if set(body) != {'selection', 'proof'}:
            raise OperationError('invalid_contract', '必须提供资产选择及浏览凭据')
        ref = await AssetReferences(ctx.state_dir, ctx.output_dir, ctx.project_id).register(
            body['selection'], body['proof'], actor=ctx.requester_user_id)
        return {'ok': True, 'data': ref.model_dump()}


@router.get('/assets/{base_id}/versions/{revision}')
async def asset_version(project: str, base_id: str, revision: int, variant_id: str | None = None,
                        user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project, user, 'viewer')
        return {'ok': True, 'data': AssetReferences(ctx.state_dir, ctx.output_dir, ctx.project_id).resolve(
            {'base_asset_id': base_id, 'variant_id': variant_id, 'revision': revision})}


@router.get('/assets/{base_id}/versions/{revision}/media')
async def resolve_asset_media(project: str, base_id: str, revision: int, slot_id: str, role: str,
                              node_type: str, variant_id: str | None = None, user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project, user, 'viewer')
        service = AssetReferences(ctx.state_dir, ctx.output_dir, ctx.project_id)
        result = await asyncio.to_thread(service.materialize,
            {'base_asset_id': base_id, 'variant_id': variant_id, 'revision': revision},
            {'slot_id': slot_id, 'role': role}, node_type)
        return {'ok': True, 'data': result['data']}


@router.get('/episodes')
async def get_episodes(project: str, user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project, user, 'viewer')
        return {'ok': True, 'data': list_episodes(ctx.state_dir)}


@router.get('/canvases')
async def get_canvases(project: str, user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project, user, 'viewer')
        return {'ok': True, 'data': list_canvases(ctx.state_dir)}


@router.post('/canvases')
async def create_canvas_workspace(project: str, body: dict, user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project, user, 'editor')
        request = CanvasCreate.model_validate(body)
        if not request.title.strip():
            raise OperationError('invalid_contract', '画布标题不能为空')
        objects = ObjectStore(ctx.state_dir)
        result = CanvasWorkspace(objects, project_id=ctx.project_id).create(ctx.requester_user_id, request)
        if result.status == 'running':
            result = await asyncio.to_thread(ProjectionService(objects).apply, result.operation_id)
        return {'ok': True, 'data': canvas_response(result)}


@router.post('/episodes')
async def create_episode(project: str, body: dict, user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project, user, 'editor')
        request = EpisodeCreate.model_validate(body)
        if not request.title.strip():
            raise OperationError('invalid_contract', '分集标题不能为空')
        objects = ObjectStore(ctx.state_dir)
        result = EpisodeWorkspace(objects, project_id=ctx.project_id).create(ctx.requester_user_id, request)
        if result.status == 'running':
            result = await asyncio.to_thread(ProjectionService(objects).apply, result.operation_id)
        return {'ok': True, 'data': episode_response(result)}


@router.post('/operations')
async def apply_operation(project: str, body: dict, user: dict = Depends(get_api_user)):
    with errors():
        actor, service = await service_for(project, user, 'editor')
        result = await asyncio.to_thread(service.apply, actor, OperationRequest.model_validate(body))
        if result.status == 'running':
            result = await asyncio.to_thread(ProjectionService(service.objects).apply, result.operation_id)
        return {'ok': True, 'data': result.model_dump()}


@router.get('/operations/{operation_id}')
async def get_operation(project: str, operation_id: str, user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project, user, 'viewer')
        path = Path(ctx.state_dir) / 'creation' / 'objects.sqlite3'
        row = None
        if path.is_file():
            with readonly(path) as conn:
                if table(conn, 'operations'):
                    row = conn.execute('SELECT result FROM operations WHERE id=?', (operation_id,)).fetchone()
        if row is None:
            raise OperationError('scope_violation', '操作不存在')
        return {'ok': True, 'data': json.loads(row['result'])}


@router.get('/assets/{base_id}/uses')
async def asset_uses(project: str, base_id: str, user: dict = Depends(get_api_user)):
    with errors():
        ctx = await context_for(project, user, 'viewer')
        return {'ok': True, 'data': AssetReferences(ctx.state_dir, ctx.output_dir, ctx.project_id).uses(base_id)}
