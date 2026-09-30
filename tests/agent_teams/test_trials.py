from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest

from novelvideo.agent_teams.trial_store import TrialStore, TrialConflict
from novelvideo.agent_teams.trials import execute_side, freeze_methods, candidate
from novelvideo.agent_teams.models import ExecutionSnapshot, MethodConfig
from novelvideo.text_task_runtime.models import AgentTaskRoute


def snap(side='active',kind='brief'):
    return ExecutionSnapshot(id=side,project_id='p',template_id='builtin',template_revision=1,active_revision=1,
        role_id='writer',subtask_id=kind,input_revision='r',input_hash='h',resolved_method=MethodConfig(prompt=side),
        resolved_model=AgentTaskRoute(model='frozen-model')).model_dump(mode='json')


def make(store):
    return store.create('p','id','hash',{'role_id':'writer','subtask_id':'brief','frozen_input':{'saved':'text'},
                                       'methods':{s:snap(s) for s in ('active','draft')}})[0]


def test_idempotency_retry_reservation_and_other_side(tmp_path):
    store = TrialStore(tmp_path/'trials.db')
    make(store)
    assert not store.create('p','id','hash',{})[1]
    with pytest.raises(TrialConflict):
        store.create('p','id','different',{})
    store.transition('p','id','active',1,{'queued'},status='completed',candidate={'markdown':'kept'})
    store.transition('p','id','draft',1,{'queued'},status='failed')
    def retry(_):
        try:
            return store.retry('p','id','draft')
        except TrialConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(v is not None for v in pool.map(retry,range(2))) == 1
    value = store.get('p','id')
    assert value['sides']['active']['candidate'] == {'markdown':'kept'}
    assert value['sides']['draft']['attempt'] == 2
    assert store.transition('p','id','draft',1,{'queued'},status='completed') is None


@pytest.mark.asyncio
async def test_identical_frozen_input_failure_isolated_and_cancel_no_publish(tmp_path,monkeypatch):
    from novelvideo.agent_teams.runtime import current_method
    from novelvideo.task_backend.cancel import TaskCancelled
    store = TrialStore(tmp_path/'trials.db')
    make(store)
    seen=[]
    async def fake(role,kind,input):
        seen.append(input.copy())
        if current_method(role,kind).resolved_method.prompt == 'draft':
            raise ValueError('provider failed')
        return {'markdown':'candidate'}
    monkeypatch.setattr('novelvideo.agent_teams.trials.candidate',fake)
    await execute_side(store,'p','id','active',1)
    with pytest.raises(ValueError):
        await execute_side(store,'p','id','draft',1)
    assert seen == [{'saved':'text'},{'saved':'text'}]
    assert store.get('p','id')['sides']['active']['status'] == 'completed'
    store.retry('p','id','draft')
    def cancel():
        raise TaskCancelled()
    with pytest.raises(TaskCancelled):
        await execute_side(store,'p','id','draft',2,cancel)
    assert store.get('p','id')['sides']['draft']['candidate'] is None
    assert not list(tmp_path.glob('data.db'))


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['brief','outline','people','scenes','props','episode_synopsis','episode_script'])
async def test_native_writer_fixed_model_and_contract(tmp_path,monkeypatch,kind):
    from dataclasses import asdict
    from novelvideo.script_creation.store import DocumentStore
    from novelvideo.agent_teams.runtime import method_scope
    docs=DocumentStore(tmp_path/'data.db')
    await docs.initialize()
    doc=await docs.create(kind='brief',title='saved',markdown='已保存的故事需求与人物冲突',client_mutation_id='create')
    calls=[]
    class Fake:
        async def run_structured(self,**kwargs):
            calls.append(kwargs)
            return {'markdown':'这是一个足够完整的候选故事内容，主人公面临真实冲突并做出关键选择。'}
    def build(route):
        assert route.model == 'frozen-model'
        fake=Fake(); fake.snapshot=route
        return fake
    monkeypatch.setattr('novelvideo.agent_teams.adapters.build_text_task_runtime',build)
    before=(tmp_path/'data.db').read_bytes()
    with method_scope([snap(kind=kind)],project_id='p'):
        result=await candidate('writer',kind,{'documents':[asdict(doc)],'episode':1,'script_mode':'single','episode_count':1,'instruction':''})
    assert result['validation_report']['passed']
    assert '不得输出交付状态' in calls[0]['system_prompt']
    assert '已保存的故事需求' in calls[0]['prompt']
    assert (tmp_path/'data.db').read_bytes() == before


def test_no_active_baseline_and_frozen_draft(tmp_path):
    from novelvideo.agent_teams.service import AgentTeamService
    from novelvideo.agent_teams.store import AgentTeamStore, RevisionConflict
    service=AgentTeamService(AgentTeamStore(tmp_path/'teams.db'),None,'user')
    service.save_draft('p',{'overrides':{'writer':{'brief':{'prompt':'original'}}}},0)
    request=SimpleNamespace(expected_draft_revision=1,role_id='writer',subtask_id='brief')
    frozen=freeze_methods(service,'p',request,{'saved':'v1'},AgentTaskRoute(model='old-model'))
    service.save_draft('p',{'overrides':{'writer':{'brief':{'prompt':'changed'}}}},1)
    assert frozen['active']['resolved_method']['prompt'] == ''
    assert frozen['draft']['resolved_method']['prompt'] == 'original'
    assert frozen['active']['input_hash'] == frozen['draft']['input_hash']
    with pytest.raises(RevisionConflict):
        freeze_methods(service,'p',request,{},AgentTaskRoute())


def test_runner_real_task_identity_cancel_checkpoint(tmp_path,monkeypatch):
    from novelvideo.task_backend.runners.agent_team_trial import runner
    store=TrialStore(tmp_path/'agent-team-trials.db')
    make(store)
    checks=[]
    async def cancelled(**kwargs):
        checks.append(kwargs)
        return False
    async def output(*args): return {'markdown':'candidate'}
    monkeypatch.setattr('novelvideo.task_backend.cancel.is_cancel_requested',cancelled)
    monkeypatch.setattr('novelvideo.agent_teams.trials.candidate',output)
    envelope={'payload':{'project_id':'p','trial_id':'id','side':'active','attempt':1},
              'project_id':'p','episode':1,'scope':'trial:id:active:1','__run_task_id':'real-task-id'}
    result=runner('writer')(envelope,SimpleNamespace(project_id='p',state_dir=tmp_path))
    assert result == {'markdown':'candidate'}
    assert len(checks)>=2
    assert all(c['task_id']=='real-task-id' for c in checks)


def test_watcher_timeout_is_retryable_failure(tmp_path,monkeypatch):
    import asyncio
    from novelvideo.task_backend.runners.agent_team_trial import runner
    from novelvideo.task_backend.cancel import TaskTimedOut
    store=TrialStore(tmp_path/'agent-team-trials.db'); make(store)
    async def timedout(coro,*args,**kwargs):
        task=asyncio.create_task(coro)
        await asyncio.sleep(0)
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
        raise TaskTimedOut(timeout_seconds=1)
    async def checkpoint(*args,**kwargs): pass
    async def waiting(*args): await asyncio.sleep(10)
    monkeypatch.setattr('novelvideo.task_backend.runners.agent_team_trial.await_envelope_with_cancel_watch',timedout)
    monkeypatch.setattr('novelvideo.task_backend.runners.agent_team_trial.raise_if_envelope_cancel_requested',lambda *a,**k:None)
    monkeypatch.setattr('novelvideo.agent_teams.trials.candidate',waiting)
    envelope={'payload':{'project_id':'p','trial_id':'id','side':'active','attempt':1},'__run_task_id':'task'}
    with pytest.raises(TaskTimedOut):
        runner('writer')(envelope,SimpleNamespace(project_id='p',state_dir=tmp_path))
    assert store.get('p','id')['sides']['active']['status']=='failed'
    assert store.retry('p','id','active')['sides']['active']['attempt']==2


@pytest.mark.asyncio
async def test_native_semantics_and_director_validate_without_store_writes(monkeypatch):
    from novelvideo.agent_teams.runtime import method_scope
    from novelvideo.screenplay_semantics.parser import parse_screenplay_document
    from novelvideo.director_plan.planner import DirectorPlanInput
    from novelvideo.director_plan.models import SourceSpan
    text='1-1 房间 日 内\n△林默推开木门。'
    scene=parse_screenplay_document(text).scenes[0]
    outputs=[{'scene_id':scene.id,'beats':[]},{'groups':[]}]
    calls=[]
    class Fake:
        async def run_structured(self,**kwargs):
            calls.append(kwargs)
            return kwargs['output_type'].model_validate(outputs.pop(0))
    def build(route):
        fake=Fake(); fake.snapshot=route
        return fake
    monkeypatch.setattr('novelvideo.agent_teams.adapters.build_text_task_runtime',build)
    def forbidden(*args,**kwargs): raise AssertionError('production write')
    monkeypatch.setattr('novelvideo.director_plan.store.DirectorPlanStore.save',forbidden)
    semantic={**snap(),'role_id':'script_parser','subtask_id':'screenplay_semantics'}
    with method_scope([semantic],project_id='p'):
        result=await candidate('script_parser','screenplay_semantics',{'content':text})
    assert result['validation_report']['passed'] is False
    value=DirectorPlanInput(episode=1,source_script_hash='hash',source_spans=(SourceSpan(id='s',ordinal=1,scene='room',time='day',text='opens door'),),
                            relevant_bible={},aspect_ratio='9:16',style_director={},project_style_snapshot_id='style')
    director={**snap(),'role_id':'director','subtask_id':'director_plan'}
    with method_scope([director],project_id='p'):
        result=await candidate('director','director_plan',value.model_dump(mode='json'))
    assert result['validation_report']['passed'] is False
    assert result['plan']['director_model']=='frozen-model'
    assert len(calls)==2
