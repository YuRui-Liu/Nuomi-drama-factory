import asyncio
import json

import pytest

from novelvideo.creation import agent_model
from novelvideo.creation.agent_runs import AgentRuns
from novelvideo.creation.library_references import digest
from novelvideo.creation.operations import OperationError
from novelvideo.freezone.canvas_store import canvas_path
from .test_agent_tools import ctx,ready,service,task,action


@pytest.fixture
def runs(ctx,ready,monkeypatch):
    async def resolve(role):return ctx
    monkeypatch.setattr(agent_model,'availability',lambda _: {
        'available':True,'code':'configured_not_verified','configuration_fingerprint':'config-1',
        'route':{'runtime':'model_api','model':'test','task_role':'creation_agent_decision','source':'global'}})
    return AgentRuns(ctx,username='alice',canvas_id=ready[0],conversation_id=ready[1],resolve_context=resolve)


def start_request(task,**changes):
    return {'request_id':'start','delegation_id':task['delegation_id'],'contract_hash':digest(task['contract']),
            'max_model_calls':1,'specialist_ids':[],**changes}


def decision(*actions,finished=True,unmet=None,delegations=None):
    return {'decision':{'message':'方案已准备','actions':[{'operation':a['operation'],'payload':a['payload']} for a in actions],
        'delegations':delegations or [],'finished':finished,'unmet':unmet or []},
        'usage':{'input_tokens':50,'output_tokens':20,'response_received':True},'cost':{'status':'unknown','amount':None}}


async def test_actual_broker_two_candidates_and_no_repeat_model(ctx,ready,task,runs,monkeypatch):
    calls=[]
    async def model(ctx,route,prompt,**identity):
        calls.append((prompt,identity))
        return decision(action(),action(title='第二方案',text='她发现收信人竟是自己。'))
    monkeypatch.setattr(agent_model,'run_decision',model)
    original=canvas_path(ctx.state_dir,ready[0]).read_bytes()
    result=await runs.start(start_request(task))
    assert result['run']['state']=='queued'
    await runs.drive(result['run']['id'])
    current=await runs.get(result['run']['id'])
    assert current['state']=='delivered' and len(current['outputs'])==2
    assert current['rounds'][0]['usage']['input_tokens']==50
    assert current['cost']['status']=='unknown'
    assert task['contract']['agent']['effective']['methods'] in calls[0][0]
    assert '开场 A' in calls[0][0]
    assert canvas_path(ctx.state_dir,ready[0]).read_bytes()==original
    assert await runs.start(start_request(task))==result
    await runs.drive(result['run']['id'])
    assert len(calls)==1


async def test_unknown_model_never_retried_on_resume(ctx,ready,task,runs,monkeypatch):
    calls=[]
    async def model(*a,**k):calls.append(1);raise TimeoutError('private provider details')
    monkeypatch.setattr(agent_model,'run_decision',model)
    value=await runs.start(start_request(task));identifier=value['run']['id']
    await runs.drive(identifier)
    current=await runs.get(identifier)
    assert current['state']=='submission_unknown' and not current['outputs']
    assert 'private provider' not in json.dumps(current)
    with pytest.raises(OperationError):
        await runs.control(identifier,'resume',{'request_id':'resume','expected_revision':current['revision']})
    await runs.drive(identifier)
    assert calls==[1]


async def test_stop_while_model_running_fences_late_result(ctx,ready,task,runs,monkeypatch):
    entered=asyncio.Event();release=asyncio.Event()
    async def model(*a,**k):
        entered.set()
        try:await release.wait()
        except asyncio.CancelledError:pass
        return decision(action())
    monkeypatch.setattr(agent_model,'run_decision',model)
    identifier=(await runs.start(start_request(task)))['run']['id']
    work=asyncio.create_task(runs.drive(identifier));await entered.wait()
    current=await runs.get(identifier)
    await runs.control(identifier,'stop',{'request_id':'stop','expected_revision':current['revision']})
    release.set();await work
    final=await runs.get(identifier)
    assert final['state']=='stopped' and not final['outputs']


async def test_input_changed_during_model_prevents_broker_effect(ctx,ready,task,runs,monkeypatch):
    async def model(*a,**k):
        path=canvas_path(ctx.state_dir,ready[0]);g=json.loads(path.read_text())
        g['nodes'][1]['data']['text']='人工作品';path.write_text(json.dumps(g))
        return decision(action())
    monkeypatch.setattr(agent_model,'run_decision',model)
    identifier=(await runs.start(start_request(task)))['run']['id']
    await runs.drive(identifier)
    result=await runs.get(identifier)
    assert result['state']=='blocked' and not result['outputs']
    assert result['error']['code']=='stale_input'


async def test_call_limit_and_partial_are_real(ctx,ready,task,runs,monkeypatch):
    calls=[]
    async def model(*a,**k):calls.append(1);return decision(action(),finished=False,unmet=['尚缺第二方案'])
    monkeypatch.setattr(agent_model,'run_decision',model)
    identifier=(await runs.start(start_request(task)))['run']['id']
    await runs.drive(identifier)
    result=await runs.get(identifier)
    assert result['state']=='partial' and len(result['outputs'])==1
    assert result['unmet']==['尚缺第二方案'] and calls==[1]


async def test_same_request_changed_limit_conflicts_and_unavailable_never_starts(ctx,ready,task,runs,monkeypatch):
    monkeypatch.setattr(agent_model,'availability',lambda _: {'available':False,'code':'runtime_unconfigured'})
    with pytest.raises(OperationError):await runs.start(start_request(task))
    assert await runs.catalog(task['delegation_id'])==[]


async def test_explicit_resume_queued_and_saved_decision_without_second_model(task,runs,monkeypatch):
    calls=[]
    async def model(*a,**k):calls.append(1);return decision(action())
    monkeypatch.setattr(agent_model,'run_decision',model)
    identifier=(await runs.start(start_request(task)))['run']['id']
    queued=await runs.get(identifier)
    await runs.control(identifier,'resume',{'request_id':'recover-queued','expected_revision':queued['revision']})
    from novelvideo.creation import agent_runs
    def crash(phase):
        if phase=='after_decision':raise asyncio.CancelledError()
    monkeypatch.setattr(agent_runs,'checkpoint',crash)
    with pytest.raises(asyncio.CancelledError):await runs.drive(identifier)
    saved=await runs.get(identifier)
    assert saved['state']=='applying'
    with pytest.raises(OperationError) as busy:
        await runs.control(identifier,'resume',{'request_id':'busy','expected_revision':saved['revision']})
    assert busy.value.code=='run_busy'
    monkeypatch.setattr(agent_runs.time,'time',lambda: 9999999999)
    monkeypatch.setattr(agent_runs,'checkpoint',lambda phase:None)
    await runs.control(identifier,'resume',{'request_id':'recover-decision','expected_revision':saved['revision']})
    await runs.drive(identifier)
    final=await runs.get(identifier)
    assert final['state']=='delivered' and len(final['outputs'])==1 and calls==[1]


@pytest.mark.parametrize('limit',[1,2])
async def test_real_child_delegation_shares_call_limit(task,runs,monkeypatch,limit):
    adoption=task['contract']['agent']['adoption_id']
    child={'adoption_id':adoption,'target_node_ids':['a'],'input_indices':[],
           'allowed_operations':['proposal.create'],'goal':'准备台词','acceptance':['给出文本候选']}
    calls=[]
    async def model(*a,**k):
        calls.append(1)
        return decision(action(),delegations=[child] if len(calls)==1 else [])
    monkeypatch.setattr(agent_model,'run_decision',model)
    identifier=(await runs.start(start_request(task,max_model_calls=limit,specialist_ids=[adoption])))['run']['id']
    await asyncio.wait_for(runs.drive(identifier),10)
    final=await runs.get(identifier)
    assert len(calls)==limit
    assert len(final['outputs'])==limit
    assert final['state']==('partial' if limit==1 else 'delivered'),final
    if limit==1:assert final['unmet']
    else:
        child_contract=runs.delegations.get(final['rounds'][1]['delegation_id'])['contract']
        assert child_contract['expanded_scope']['scope']['node_ids']==['a']
        assert child_contract['allowed_operations']==['proposal.create']


async def test_action_commit_crash_replays_receipt_not_candidate(task,runs,monkeypatch):
    from novelvideo.creation import agent_runs
    calls=[]
    async def model(*a,**k):calls.append(1);return decision(action())
    monkeypatch.setattr(agent_model,'run_decision',model)
    identifier=(await runs.start(start_request(task)))['run']['id']
    def crash(phase):
        if phase=='after_action':raise asyncio.CancelledError()
    monkeypatch.setattr(agent_runs,'checkpoint',crash)
    with pytest.raises(asyncio.CancelledError):await runs.drive(identifier)
    current=await runs.get(identifier)
    assert len(current['outputs'])==1
    ref=current['outputs'][0]['object_ref']
    monkeypatch.setattr(agent_runs.time,'time',lambda:9999999999)
    monkeypatch.setattr(agent_runs,'checkpoint',lambda phase:None)
    await runs.control(identifier,'resume',{'request_id':'resume','expected_revision':current['revision']})
    await runs.drive(identifier)
    result=await runs.get(identifier)
    assert result['state']=='delivered' and [o['object_ref'] for o in result['outputs']]==[ref]
    assert calls==[1]


async def test_restart_inflight_marks_unknown_without_second_request(task,runs,monkeypatch):
    from novelvideo.creation import agent_runs
    def crash(phase):
        if phase=='before_model':raise asyncio.CancelledError()
    monkeypatch.setattr(agent_runs,'checkpoint',crash)
    monkeypatch.setattr(agent_model,'run_decision',lambda *a,**k:pytest.fail('must not resend'))
    identifier=(await runs.start(start_request(task)))['run']['id']
    with pytest.raises(asyncio.CancelledError):await runs.drive(identifier)
    monkeypatch.setattr(agent_runs.time,'time',lambda:9999999999)
    current=await runs.get(identifier)
    resumed=await runs.control(identifier,'resume',{'request_id':'recover-inflight','expected_revision':current['revision']})
    assert resumed['run']['state']=='submission_unknown'
    await runs.drive(identifier)
    assert (await runs.get(identifier))['state']=='submission_unknown'


async def test_acl_removed_while_waiting_for_project_lock_prevents_start(task,runs,ctx):
    from novelvideo.production_workflow.coordination import asset_project_lock
    permitted=True
    async def resolve(role):
        if not permitted:raise OperationError('scope_violation','已失去权限')
        return ctx
    runs.resolve_context=resolve
    async with asset_project_lock(ctx.state_dir):
        pending=asyncio.create_task(runs.start(start_request(task)))
        await asyncio.sleep(.01)
        permitted=False
    with pytest.raises(OperationError):await pending
    permitted=True
    assert await runs.catalog(task['delegation_id'])==[]


async def test_revoke_between_model_and_action_retains_no_candidate(task,runs,service,monkeypatch):
    from .test_ontology import command
    async def model(*a,**k):
        await service.apply(command('delegation.revoke',{'delegation_id':task['delegation_id'],
            'expected_revision':1,'reason':'用户撤回'},'revoke-during-model'))
        return decision(action())
    monkeypatch.setattr(agent_model,'run_decision',model)
    identifier=(await runs.start(start_request(task)))['run']['id']
    await runs.drive(identifier)
    result=await runs.get(identifier)
    assert result['state']=='blocked' and not result['outputs']
    assert result['error']['code']=='delegation_inactive'


async def test_same_request_limit_change_conflict_and_config_change_no_model(task,runs,monkeypatch):
    result=await runs.start(start_request(task))
    with pytest.raises(OperationError) as conflict:
        await runs.start(start_request(task,max_model_calls=2))
    assert conflict.value.code=='idempotency_conflict'
    monkeypatch.setattr(agent_model,'availability',lambda _: {'available':False})
    monkeypatch.setattr(agent_model,'run_decision',lambda *a,**k:pytest.fail('configuration changed'))
    await runs.drive(result['run']['id'])
    current=await runs.get(result['run']['id'])
    assert current['state']=='blocked' and not current['rounds']
    assert current['error']['code']=='runtime_configuration_changed'


async def test_stop_committed_before_send_admission_never_calls_model(task,runs,monkeypatch):
    identifier=(await runs.start(start_request(task)))['run']['id']
    await runs._send('claim',{'id':identifier,'owner':'worker'})
    await runs._send('prepare',{'id':identifier,'owner':'worker'})
    stale=runs._read_value(identifier,raw=True)
    await runs.control(identifier,'stop',{'request_id':'stop-before-send','expected_revision':stale['revision']})
    async def model(*a,**k):pytest.fail('stopped before admission')
    monkeypatch.setattr(agent_model,'run_decision',model)
    with pytest.raises(OperationError) as failure:await runs._model(stale,'worker')
    assert failure.value.code=='run_fenced'


async def test_context_read_is_audited_but_is_not_delivered_candidate(task,runs,monkeypatch):
    async def model(*a,**k):return decision(action('context.read'))
    monkeypatch.setattr(agent_model,'run_decision',model)
    identifier=(await runs.start(start_request(task)))['run']['id']
    await runs.drive(identifier)
    current=await runs.get(identifier)
    assert current['state']=='partial' and current['outputs']==[]
    tools=runs._tools(task['delegation_id'])
    receipt=await tools.receipt(runs._action_id(identifier,0,0))
    assert receipt['output']['context']['nodes']


@pytest.mark.parametrize('child_run',[True,False])
async def test_unmet_is_latest_per_delegation_not_last_child(task,runs,monkeypatch,child_run):
    adoption=task['contract']['agent']['adoption_id']
    child={'adoption_id':adoption,'target_node_ids':['a'],'input_indices':[],
        'allowed_operations':['proposal.create'],'goal':'准备台词','acceptance':['给出文本候选']}
    calls=[]
    async def model(*a,**k):
        calls.append(1)
        if len(calls)==1:
            return decision(action(),finished=child_run,unmet=['主创尚缺第二方案'],
                            delegations=[child] if child_run else [])
        return decision(action())
    monkeypatch.setattr(agent_model,'run_decision',model)
    identifier=(await runs.start(start_request(task,max_model_calls=2,specialist_ids=[adoption])))['run']['id']
    await runs.drive(identifier)
    final=await runs.get(identifier)
    assert calls==[1,1] and len(final['outputs'])==2
    assert final['unmet']==(['主创尚缺第二方案'] if child_run else [])
    assert final['state']==('partial' if child_run else 'delivered')
