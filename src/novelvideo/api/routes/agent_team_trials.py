"""Saved-input candidate comparisons; model work only happens in task workers."""
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field

from novelvideo.api.auth import get_api_user, require_scope
from novelvideo.api.deps import resolve_project_scope
from novelvideo.agent_teams.models import Contract
from novelvideo.agent_teams.store import AgentTeamStore
from novelvideo.agent_teams.trial_store import TrialStore, TrialConflict
from novelvideo.agent_teams.trial_inputs import choices, freeze_input, TASK_ROLES
from novelvideo.agent_teams.trials import digest, freeze_methods
from novelvideo.ports import get_task_backend
from novelvideo.task_state import get_task_manager
from .agent_teams import personal, errors

router = APIRouter()


class DocumentRef(Contract):
    id: str
    revision_id: str


class SavedInput(Contract):
    documents: list[DocumentRef] = Field(default_factory=list, max_length=100)
    source_revision: int | None = Field(default=None, ge=1)


class TrialRequest(Contract):
    request_id: UUID
    role_id: str
    subtask_id: str
    episode: int = Field(default=1, ge=1, le=100)
    expected_draft_revision: int = Field(ge=1, strict=True)
    input: SavedInput
    instruction: str = Field(default='',max_length=8000)
    script_mode: Literal['single','series'] = 'single'
    episode_count: int = Field(default=1,ge=1,le=100)


class Retry(Contract):
    side: Literal['active','draft']


async def context(project,user,write=False):
    scope = await resolve_project_scope(project,user,required_role='editor' if write else 'viewer')
    return scope.ctx, TrialStore(Path(scope.state_dir) / 'agent-team-trials.db')


def reconcile(ctx,store,trial):
    """Reflect cancellation before runner start and outer watcher timeouts."""
    if trial is None:
        return None
    for side,state in trial['sides'].items():
        if not state.get('task_id') or state['status'] not in {'queued','running','cancelled'}:
            continue
        task = get_task_manager().get_task_for_project(ctx,'agent_team_trial_'+trial['role_id'],trial['episode'],
                    scope=f"trial:{trial['id']}:{side}:{state['attempt']}")
        if task and task.task_id == state['task_id'] and task.status in {'failed','cancelled'}:
            store.transition(ctx.project_id,trial['id'],side,state['attempt'],{state['status']},
                             status=task.status,candidate=None,error='task_'+task.status)
    return store.get(ctx.project_id,trial['id'])


async def enqueue(ctx,store,trial,side):
    state = trial['sides'][side]
    try:
        queued = await get_task_backend().enqueue_project_task(ctx,
            task_type='agent_team_trial_' + trial['role_id'],queue_kind='default',episode=trial['episode'],
            scope=f"trial:{trial['id']}:{side}:{state['attempt']}",
            payload={'trial_id':trial['id'],'side':side,'attempt':state['attempt'],'project_id':ctx.project_id})
        store.transition(ctx.project_id,trial['id'],side,state['attempt'],{'queued','running','completed','failed','cancelled'},task_id=queued.task_state.task_id)
    except Exception as exc:
        store.transition(ctx.project_id,trial['id'],side,state['attempt'],{'queued'},status='failed',error='queue_submission_failed')


@router.get('/projects/{project}/agent-team/trial-inputs')
async def trial_inputs(project: str,role_id: str,subtask_id: str,episode: int = Query(1,ge=1),user: dict=Depends(get_api_user)):
    ctx,_ = await context(project,user)
    return await choices(ctx,role_id,subtask_id,episode)


@router.post('/projects/{project}/agent-team/trials',status_code=202)
async def submit(project: str,body: TrialRequest,user: dict=Depends(require_scope('tasks:submit'))):
    ctx,store = await context(project,user,True)
    request = body.model_dump(mode='json')
    existing = store.get(ctx.project_id,str(body.request_id))
    if existing:
        if existing['request_hash'] != digest(request):
            raise HTTPException(409,'request_id already has different parameters')
        return reconcile(ctx,store,existing)
    with errors():
        if body.role_id == 'writer' and (body.episode > body.episode_count or (body.script_mode == 'single' and body.episode_count != 1)):
            raise ValueError('episode count does not match mode')
        frozen = await freeze_input(ctx,body)
        service = personal(user)
        service.store = AgentTeamStore(Path(ctx.state_dir) / 'agent-team.db')
        from novelvideo.text_task_runtime.settings import resolve_configured_agent_task_route
        from novelvideo.text_task_runtime.models import AgentTaskRoute
        route = resolve_configured_agent_task_route(ctx=ctx,task_role=TASK_ROLES[body.role_id])
        route = AgentTaskRoute.model_validate({k:v for k,v in route.model_dump().items() if k in AgentTaskRoute.model_fields})
        methods = freeze_methods(service,ctx.project_id,body,frozen,route)
        data = {'role_id':body.role_id,'subtask_id':body.subtask_id,'episode':body.episode,
                'request_hash':digest(request),'request':request,'frozen_input':frozen,'input_hash':digest(frozen),'methods':methods,
                'method_hashes':{s:digest({k:v for k,v in m.items() if k != 'id'}) for s,m in methods.items()}}
        try:
            trial,created = store.create(ctx.project_id,str(body.request_id),digest(request),data)
        except TrialConflict as exc:
            raise HTTPException(409,str(exc)) from exc
    if created:
        for side in ('active','draft'):
            await enqueue(ctx,store,trial,side)
    return store.get(ctx.project_id,trial['id'])


@router.get('/projects/{project}/agent-team/trials')
async def list_trials(project: str,user: dict=Depends(get_api_user)):
    ctx,store = await context(project,user)
    return [reconcile(ctx,store,trial) for trial in store.list(ctx.project_id)]


@router.get('/projects/{project}/agent-team/trials/{id}')
async def get_trial(project: str,id: UUID,user: dict=Depends(get_api_user)):
    ctx,store = await context(project,user)
    value = store.get(ctx.project_id,str(id))
    if value is None:
        raise HTTPException(404,'trial not found')
    return reconcile(ctx,store,value)


@router.post('/projects/{project}/agent-team/trials/{id}/retry-side',status_code=202)
async def retry_side(project: str,id: UUID,body: Retry,user: dict=Depends(require_scope('tasks:submit'))):
    ctx,store = await context(project,user,True)
    reconcile(ctx,store,store.get(ctx.project_id,str(id)))
    try:
        trial = store.retry(ctx.project_id,str(id),body.side)
    except KeyError as exc:
        raise HTTPException(404,'trial not found') from exc
    except TrialConflict as exc:
        raise HTTPException(409,str(exc)) from exc
    await enqueue(ctx,store,trial,body.side)
    return store.get(ctx.project_id,str(id))
