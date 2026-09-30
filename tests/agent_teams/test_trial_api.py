import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import agent_team_trials as routes, agent_teams
from novelvideo.agent_teams.service import AgentTeamService
from novelvideo.agent_teams.store import AgentTeamStore
from novelvideo.script_creation.store import DocumentStore
from novelvideo.text_task_runtime.models import AgentTaskRouteSnapshot


@pytest.fixture
def fixture(tmp_path,monkeypatch):
    ctx=SimpleNamespace(state_dir=tmp_path,project_id='canonical',output_dir=tmp_path/'output')
    calls=[]; queued=[]
    async def scope(project,user,required_role):
        calls.append((project,required_role))
        if project == 'forbidden':
            raise HTTPException(403,'denied')
        return SimpleNamespace(ctx=ctx,state_dir=tmp_path)
    monkeypatch.setattr(routes,'resolve_project_scope',scope)
    monkeypatch.setattr(routes,'get_task_manager',lambda:SimpleNamespace(get_task_for_project=lambda *a,**k:None))
    monkeypatch.setattr(agent_teams,'get_user_base_dir',lambda user: tmp_path/user)
    async def enqueue(ctx,**kwargs):
        queued.append((ctx.project_id,kwargs))
        return SimpleNamespace(task_state=SimpleNamespace(task_id='task-'+str(len(queued))))
    monkeypatch.setattr(routes,'get_task_backend',lambda:SimpleNamespace(enqueue_project_task=enqueue))
    monkeypatch.setattr('novelvideo.text_task_runtime.settings.resolve_configured_agent_task_route',
                        lambda **kwargs:AgentTaskRouteSnapshot(task_role='script_creation',source='project',model='frozen'))
    service=AgentTeamService(AgentTeamStore(tmp_path/'agent-team.db'),None,'alice')
    service.save_draft('canonical',{},0)
    async def document():
        store=DocumentStore(tmp_path/'data.db')
        await store.initialize()
        return await store.create(kind='brief',title='Saved',markdown='原始保存的完整故事简报',client_mutation_id='id')
    doc=asyncio.run(document())
    app=FastAPI(); app.include_router(routes.router)
    app.dependency_overrides[get_api_user]=lambda:{'username':'alice'}
    request={'request_id':str(uuid4()),'role_id':'writer','subtask_id':'brief','expected_draft_revision':1,
             'input':{'documents':[{'id':doc.id,'revision_id':doc.current_revision_id}]}}
    return SimpleNamespace(client=TestClient(app),app=app,ctx=ctx,calls=calls,queued=queued,request=request,service=service)


def test_submit_auth_canonical_and_idempotence_after_edits(fixture):
    f=fixture; path='/projects/alias/agent-team'
    choices=f.client.get(path+'/trial-inputs?role_id=writer&subtask_id=brief').json()
    assert choices['documents'][0]['revision_id'] == f.request['input']['documents'][0]['revision_id']
    response=f.client.post(path+'/trials',json=f.request)
    assert response.status_code == 202,response.text
    original=response.json()
    assert original['project_id'] == 'canonical'
    assert len(f.queued) == 2
    assert all(item[0] == 'canonical' for item in f.queued)
    f.service.save_draft('canonical',{'overrides':{'writer':{'brief':{'prompt':'changed'}}}},1)
    assert f.client.post(path+'/trials',json=f.request).json() == original
    assert len(f.queued) == 2
    assert f.client.post(path+'/trials',json={**f.request,'instruction':'different'}).status_code == 409
    assert f.client.get('/projects/forbidden/agent-team/trials').status_code == 403
    assert ('alias','editor') in f.calls
    f.app.dependency_overrides[get_api_user]=lambda:{'username':'alice','scopes':[]}
    assert f.client.post(path+'/trials',json=f.request).status_code == 403


def test_queue_failure_retry_and_saved_input_validation(fixture,monkeypatch):
    f=fixture; path='/projects/alias/agent-team/trials'
    wrong={**f.request,'input':{'documents':[{'id':'missing','revision_id':'forged'}]}}
    assert f.client.post(path,json=wrong).status_code == 422
    wrong={**f.request,'input':{'documents':[{'id':f.request['input']['documents'][0]['id'],'revision_id':'forged'}]}}
    assert f.client.post(path,json=wrong).status_code == 409
    assert f.client.post(path,json={**f.request,'expected_draft_revision':2}).status_code == 409
    async def fail(ctx,**kwargs):
        raise RuntimeError('queue unavailable')
    monkeypatch.setattr(routes,'get_task_backend',lambda:SimpleNamespace(enqueue_project_task=fail))
    result=f.client.post(path,json=f.request).json()
    assert all(s['status']=='failed' for s in result['sides'].values())
    retried=f.client.post(path+'/'+result['id']+'/retry-side',json={'side':'draft'}).json()
    assert retried['sides']['draft']['attempt']==2
    assert retried['sides']['active']['attempt']==1


@pytest.mark.parametrize('role,subtask',[('director','director_plan'),('script_parser','screenplay_semantics')])
def test_nonwriter_episode_two_does_not_require_writer_episode_count(fixture,monkeypatch,role,subtask):
    f=fixture
    async def frozen(ctx,request):
        return {'episode':request.episode,'content':'frozen'}
    monkeypatch.setattr(routes,'freeze_input',frozen)
    request={**f.request,'role_id':role,'subtask_id':subtask,'episode':2,'input':{'source_revision':1}}
    response=f.client.post('/projects/alias/agent-team/trials',json=request)
    assert response.status_code==202,response.text
    assert len(f.queued)==2


def test_director_missing_semantics_is_actionable(fixture,monkeypatch):
    from novelvideo.agent_teams.trial_inputs import freeze_input
    from novelvideo.task_backend.runners.director_plan import DirectorPlanTaskError
    f=fixture
    source=SimpleNamespace(episode_number=2,source_revision=1,content='saved')
    async def repository(ctx): return object()
    async def sources(self): return [source]
    async def build(payload,ctx): raise DirectorPlanTaskError('SCREENPLAY_SEMANTICS_REQUIRED')
    monkeypatch.setattr('novelvideo.api.deps.make_sqlite_store_for_context',repository)
    monkeypatch.setattr('novelvideo.episode_source_store.EpisodeSourceStore.__init__',lambda self,store:None)
    monkeypatch.setattr('novelvideo.episode_source_store.EpisodeSourceStore.list_sources',sources)
    monkeypatch.setattr('novelvideo.task_backend.runners.director_plan._build_director_plan_input',build)
    request={**f.request,'role_id':'director','subtask_id':'director_plan','episode':2,'input':{'source_revision':1}}
    response=f.client.post('/projects/alias/agent-team/trials',json=request)
    assert response.status_code==422,response.text
    assert response.json()['detail']['code']=='SCREENPLAY_SEMANTICS_REQUIRED'
    assert not f.queued


def test_cancelled_before_worker_reconciles_and_can_retry(fixture,monkeypatch):
    f=fixture; path='/projects/alias/agent-team/trials'
    trial=f.client.post(path,json=f.request).json()
    task=SimpleNamespace(task_id=trial['sides']['active']['task_id'],status='cancelled')
    monkeypatch.setattr(routes,'get_task_manager',lambda:SimpleNamespace(get_task_for_project=lambda *a,**k:task))
    result=f.client.get(path+'/'+trial['id']).json()
    assert result['sides']['active']['status']=='cancelled'
    assert result['sides']['draft']['status']=='queued'
    result=f.client.post(path+'/'+trial['id']+'/retry-side',json={'side':'active'}).json()
    assert result['sides']['active']['attempt']==2
    assert result['sides']['active']['status']=='queued'
