"""Recoverable character-reference repair commands."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import resolve_project_scope, make_sqlite_store_for_context
from novelvideo.narrative_groups.character_reference_repair import RepairStore, repair_lock, create_repair, select_repair, bind_repair, choices, assert_source, recover_adopted_repair

router = APIRouter()
BASE = '/projects/{project}/episodes/{episode}/director-plans/{revision_id}/character-reference-repairs'


class CreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    binding_id: str = Field(min_length=1)
    expected_reference_revision: str = Field(min_length=1)
    request_id: str = Field(min_length=1, max_length=200)
    group_id: str = ''


class SelectRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_reference_revision: str = Field(min_length=1)
    mode: Literal['existing', 'create']
    character_name: str = Field(min_length=1)
    identity_id: str | None = None
    appearance: str = ''
    costume: str = ''


class BindRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_reference_revision: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)


async def execute(project, episode, revision_id, user, action, repair_id=None, body=None):
    resolved = await resolve_project_scope(project, user, required_role='viewer' if action == 'get' else 'editor')
    ctx = resolved.ctx
    sql = await make_sqlite_store_for_context(ctx)
    try:
        async with repair_lock(ctx):
            reload_catalog = getattr(sql, 'load_graph_state', None)
            if callable(reload_catalog):
                await reload_catalog()
            journal = RepairStore(ctx.state_dir, ctx.project_id)
            item = journal.get(repair_id) if repair_id else None
            if repair_id and (item is None or item['episode_number'] != episode or item['source_plan_revision_id'] != revision_id):
                raise HTTPException(404, 'repair not found')
            if action == 'create':
                item = await create_repair(ctx, sql, episode, revision_id, body)
            elif action == 'select':
                item = await select_repair(ctx, sql, item, body)
            elif action == 'bind':
                item = await bind_repair(ctx, sql, item, body.candidate_id, body.expected_reference_revision)
            else:
                try:
                    assert_source(ctx, item)
                    item = await recover_adopted_repair(ctx, sql, item, can_bind=getattr(ctx, 'effective_role', 'viewer') in ('editor', 'owner', 'admin'))
                except ValueError as exc:
                    item.update(status='source_changed', error=str(exc))
            return {'ok': True, 'data': {**item, 'existing_choices': choices(sql)}}
    except ValueError as exc:
        raise HTTPException(409, detail={'code': str(exc), 'error': str(exc)}) from exc
    finally:
        await sql.close()


@router.post(BASE)
async def create(project: str, episode: int, revision_id: str, body: CreateRequest, user: dict = Depends(get_api_user)):
    return await execute(project, episode, revision_id, user, 'create', body=body)


@router.get(BASE + '/{repair_id}')
async def get(project: str, episode: int, revision_id: str, repair_id: str, user: dict = Depends(get_api_user)):
    return await execute(project, episode, revision_id, user, 'get', repair_id)


@router.patch(BASE + '/{repair_id}')
async def select(project: str, episode: int, revision_id: str, repair_id: str, body: SelectRequest, user: dict = Depends(get_api_user)):
    return await execute(project, episode, revision_id, user, 'select', repair_id, body)


@router.post(BASE + '/{repair_id}/bind')
async def bind(project: str, episode: int, revision_id: str, repair_id: str, body: BindRequest, user: dict = Depends(get_api_user)):
    return await execute(project, episode, revision_id, user, 'bind', repair_id, body)


async def bind_after_adoption(ctx, sql, name, identity_id, candidate_id):
    async with repair_lock(ctx):
        journal = RepairStore(ctx.state_dir, ctx.project_id)
        result = []
        for item in journal.all():
            if item['status'] in ('selected', 'binding_failed') and (item['character_name'], item['identity_id']) == (name, identity_id):
                result.append(await bind_repair(ctx, sql, item, candidate_id))
        return result
