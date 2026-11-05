import asyncio
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace

import pytest

from novelvideo.creation import agent_model
from novelvideo.creation import agent_runs
from novelvideo.creation.agent_runs import AgentRuns, _RunTools
from novelvideo.creation.library_references import digest
from novelvideo.creation.operations import OperationError
from novelvideo.freezone.canvas_store import canvas_path
from .test_agent_tools import ctx,ready,service,task,action
from .test_delegations import create as create_delegation, selection as delegation_selection
from .test_ontology import command


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


async def _create_child_delegations(ctx,ready,task,count=2):
    from novelvideo.creation.delegations import Delegations
    from .test_delegations import create,selection

    parent_id=task['delegation_id']
    adoption_id=task['contract']['agent']['adoption_id']
    children=Delegations(ctx,username='alice',authority='agent',parent_id=parent_id)
    result=[]
    for index,node_id in enumerate(('a','b','a','b')[:count]):
        body=selection(ready,parent_id=parent_id,scope={'kind':'nodes','node_ids':[node_id]},
            agent={'kind':'adoption','adoption_id':adoption_id,'revision':1},
            goal=f'准备节点 {node_id} 的台词',allowed_operations=['proposal.create'])
        result.append(await create(children,body,f'child-delegation-{index}'))
    return result


def _start_for_delegation(delegation,request_id,*,max_model_calls=1):
    return {'request_id':request_id,'delegation_id':delegation['delegation_id'],
        'contract_hash':digest(delegation['contract']),'max_model_calls':max_model_calls,'specialist_ids':[]}


async def test_sibling_runs_compete_for_the_last_root_model_call(ctx,ready,task,runs,monkeypatch):
    root=(await runs.start(start_request(task,max_model_calls=1)))['run']
    children=await _create_child_delegations(ctx,ready,task)
    child_runs=[(await runs.start(_start_for_delegation(child,f'start-child-{index}',max_model_calls=24)))['run']
                for index,child in enumerate(children)]
    sent=[]

    async def model(*args,**identity):
        sent.append(identity['attempt_id'])
        return decision()

    original_model=runs._model
    ready_to_admit=asyncio.Event()
    waiting=0
    async def race_last_slot(value,owner):
        nonlocal waiting
        waiting+=1
        if waiting==2:
            ready_to_admit.set()
        await asyncio.wait_for(ready_to_admit.wait(),2)
        return await original_model(value,owner)

    monkeypatch.setattr(agent_model,'run_decision',model)
    monkeypatch.setattr(runs,'_model',race_last_slot)
    await asyncio.gather(*(runs.drive(run['id']) for run in child_runs))

    assert len(sent)==1
    assert all(run['root_delegation_id']==task['delegation_id'] for run in child_runs)
    assert all(run['max_model_calls']==root['max_model_calls'] for run in child_runs)
    finals=[await runs.get(run['id']) for run in child_runs]
    assert all(run['state']!='submission_unknown' for run in finals)
    assert all(run['remaining_model_calls']==0 for run in finals)


async def test_unknown_sibling_attempt_keeps_the_shared_root_slot(ctx,ready,task,runs,monkeypatch):
    await runs.start(start_request(task,max_model_calls=1))
    children=await _create_child_delegations(ctx,ready,task)
    child_runs=[(await runs.start(_start_for_delegation(child,f'unknown-child-{index}')))['run']
                for index,child in enumerate(children)]
    sent=[]

    async def model(*args,**identity):
        sent.append(identity['attempt_id'])
        raise TimeoutError('provider outcome unknown')

    monkeypatch.setattr(agent_model,'run_decision',model)
    await runs.drive(child_runs[0]['id'])
    unknown=await runs.get(child_runs[0]['id'])
    await runs.drive(child_runs[1]['id'])
    waiting=await runs.get(child_runs[1]['id'])

    assert unknown['state']=='submission_unknown'
    assert unknown['remaining_model_calls']==0
    assert waiting['state']=='partial'
    assert waiting['remaining_model_calls']==0
    assert len(sent)==1
    with runs.read() as conn:
        attempt=conn.execute('SELECT state FROM agent_run_model_attempts WHERE attempt_id=?',
                             (sent[0],)).fetchone()
        budget=conn.execute('SELECT used_model_calls FROM agent_run_model_budgets WHERE root_delegation_id=?',
                             (task['delegation_id'],)).fetchone()
    assert attempt['state']=='unknown' and budget['used_model_calls']==1


async def test_same_attempt_admission_replay_reserves_only_once(task,runs):
    run=(await runs.start(start_request(task,max_model_calls=2)))['run']
    await runs._send('claim',{'id':run['id'],'owner':'worker'})
    await runs._send('prepare',{'id':run['id'],'owner':'worker'})

    request_id='admit-model-once'
    first=await runs._send('admit_model',{'id':run['id'],'owner':'worker'},request_id)
    replay=await runs._send('admit_model',{'id':run['id'],'owner':'worker'},request_id)
    same_attempt=await runs._send('admit_model',{'id':run['id'],'owner':'worker'})
    assert first['run']['rounds'][0]['attempt_id']==replay['run']['rounds'][0]['attempt_id']
    assert first['run']['rounds'][0]['attempt_id']==same_attempt['run']['rounds'][0]['attempt_id']
    assert first['model_send_admitted'] is True
    assert replay['model_send_admitted'] is False
    assert same_attempt['model_send_admitted'] is False
    assert same_attempt['run']['remaining_model_calls']==1
    assert (await runs.receipt(request_id))['model_send_admitted'] is True
    with runs.read() as conn:
        attempts=conn.execute('SELECT COUNT(*) FROM agent_run_model_attempts WHERE run_id=?',
                              (run['id'],)).fetchone()[0]
        budget=conn.execute('SELECT used_model_calls FROM agent_run_model_budgets WHERE root_delegation_id=?',
                             (task['delegation_id'],)).fetchone()[0]
    assert attempts==budget==1


async def test_same_admitted_attempt_only_starts_one_model_send(task,runs,monkeypatch):
    run=(await runs.start(start_request(task,max_model_calls=2)))['run']
    owner='duplicate-model-owner'
    await runs._send('claim',{'id':run['id'],'owner':owner})
    await runs._send('prepare',{'id':run['id'],'owner':owner})
    value=runs._read_value(run['id'],raw=True)
    sent=[]

    async def model(*args,**identity):
        sent.append(identity['attempt_id'])
        return decision()

    monkeypatch.setattr(agent_model,'run_decision',model)
    await runs._model(value,owner)
    with pytest.raises(OperationError) as failure:
        await runs._model(value,owner)

    assert failure.value.code=='model_submission_unknown'
    assert sent==[value['rounds'][-1]['attempt_id']]
    assert (await runs.get(run['id']))['remaining_model_calls']==1


@pytest.mark.parametrize(('response_received', 'expected_state', 'expected_phase', 'attempt_state'), [
    (False, 'submission_unknown', 'submission_unknown', 'unknown'),
    (True, 'blocked', 'invalid_response', 'response_received'),
])
async def test_startup_recovery_classifies_expired_model_submission_once(
        task,runs,response_received,expected_state,expected_phase,attempt_state):
    run=(await runs.start(start_request(task,max_model_calls=2)))['run']
    owner='interrupted-startup-worker'
    await runs._send('claim',{'id':run['id'],'owner':owner})
    await runs._send('prepare',{'id':run['id'],'owner':owner})
    admitted=await runs._send('admit_model',{'id':run['id'],'owner':owner},'startup-recovery-admit')
    attempt_id=admitted['run']['rounds'][-1]['attempt_id']

    db_path=runs.ctx.state_dir/'creation'/'objects.sqlite3'
    with sqlite3.connect(db_path) as conn:
        value=json.loads(conn.execute('SELECT content FROM agent_runs WHERE id=?',(run['id'],)).fetchone()[0])
        value['lease']['expires']=time.time()-1
        if response_received:
            value['rounds'][-1].update(response_received=True,
                                       usage={'response_received':True,'input_tokens':12})
            conn.execute("UPDATE agent_run_model_attempts SET state='response_received' WHERE attempt_id=?",
                         (attempt_id,))
        conn.execute('UPDATE agent_runs SET content=? WHERE id=?',
                     (json.dumps(value,ensure_ascii=False,separators=(',',':')),run['id']))
        conn.commit()

    assert agent_runs.recover_expired_model_submissions(runs.ctx.state_dir)==1
    recovered=await runs.get(run['id'])
    assert recovered['state']==expected_state
    assert recovered['rounds'][-1]['phase']==expected_phase
    assert recovered.get('lease') is None
    with runs.read() as conn:
        attempt=conn.execute('SELECT state FROM agent_run_model_attempts WHERE attempt_id=?',
                             (attempt_id,)).fetchone()
    assert attempt['state']==attempt_state
    assert agent_runs.recover_expired_model_submissions(runs.ctx.state_dir)==0


async def test_child_run_cannot_create_a_root_budget_without_root_run(ctx,ready,task,runs,monkeypatch):
    child= (await _create_child_delegations(ctx,ready,task,count=1))[0]
    monkeypatch.setattr(agent_model,'run_decision',lambda *args,**kwargs:pytest.fail('model must not run'))

    with pytest.raises(OperationError) as failure:
        await runs.start(_start_for_delegation(child,'orphan-child',max_model_calls=24))

    assert failure.value.code=='root_model_budget_missing'


async def test_first_root_run_migrates_legacy_child_pending_and_unknown_calls(ctx,ready,task,runs,monkeypatch):
    from novelvideo.creation.store import ObjectStore
    from novelvideo.creation.library_references import packed

    child=(await _create_child_delegations(ctx,ready,task,count=1))[0]
    legacy={'id':'legacy-child-run','revision':1,'state':'submission_unknown','delegation_id':child['delegation_id'],
        'max_model_calls':24,'rounds':[{'attempt_id':'legacy-pending','phase':'model_inflight'},
                                      {'attempt_id':'legacy-unknown','phase':'submission_unknown'}]}
    with ObjectStore(ctx.state_dir).transaction() as conn:
        for statement in runs.schema:
            conn.execute(statement)
        conn.execute('INSERT INTO agent_runs VALUES (?,?,?,?,?,?,?,?,?)',
            (legacy['id'],ctx.project_id,ctx.requester_user_id,runs.username,runs.canvas_id,
             runs.conversation_id,child['delegation_id'],legacy['revision'],packed(legacy)))

    root=(await runs.start(start_request(task,max_model_calls=1)))['run']
    with runs.read() as conn:
        budget=conn.execute('SELECT * FROM agent_run_model_budgets WHERE root_delegation_id=?',
                             (task['delegation_id'],)).fetchone()

    assert budget['max_model_calls']==1
    assert budget['used_model_calls']==2
    assert root['remaining_model_calls']==0
    monkeypatch.setattr(agent_model,'run_decision',lambda *args,**kwargs:pytest.fail('legacy overage must not send'))
    await runs.drive(root['id'])
    assert (await runs.get(root['id']))['state']=='partial'


async def test_root_budgets_are_isolated(ctx,ready,task,runs,service):
    from .test_delegations import create,selection

    other=await create(service,selection(ready),request_id='other-root')
    first=(await runs.start(start_request(task,max_model_calls=2)))['run']
    second=(await runs.start(_start_for_delegation(other,'start-other-root',max_model_calls=3)))['run']
    for run in (first,second):
        await runs._send('claim',{'id':run['id'],'owner':run['id']})
        await runs._send('prepare',{'id':run['id'],'owner':run['id']})
        await runs._send('admit_model',{'id':run['id'],'owner':run['id']})
    assert (await runs.get(first['id']))['remaining_model_calls']==1
    assert (await runs.get(second['id']))['remaining_model_calls']==2


async def test_child_limit_does_not_expand_frozen_root_budget(ctx,ready,task,runs):
    root=(await runs.start(start_request(task,max_model_calls=1)))['run']
    child=(await _create_child_delegations(ctx,ready,task,count=1))[0]
    child_run=(await runs.start(_start_for_delegation(child,'large-child-limit',max_model_calls=24)))['run']

    assert root['root_max_model_calls']==1
    assert child_run['root_delegation_id']==task['delegation_id']
    assert child_run['max_model_calls']==1
    with pytest.raises(OperationError):
        await runs.start({**_start_for_delegation(child,'client-root',max_model_calls=24),
                          'root_delegation_id':'forged-root'})


async def test_trusted_start_pins_specialist_revision_from_intake(ready,task,runs):
    from .test_ontology import command

    profiles=ready[2]
    entry=next(item for item in profiles.builtin_catalog()
               if item['source']['ref']['id']=='builtin:creative-ideator')
    adopted=await profiles.apply(command('adopt',{'source':'builtin','ref':entry['source']['ref'],
        'proof':entry['proof']},'pinned-specialist'))
    changes={'methods':'intake 时冻结的专业方法'}
    preview=profiles.override_preview(adopted['adoption_id'],changes)
    await profiles.apply(command('override',{'adoption_id':adopted['adoption_id'],
        'expected_revision':1,'changes':changes,'proof':preview['proof']},'pinned-specialist-update'))

    result=await runs.start_with_pinned_specialists({
        'request_id':'start-pinned-specialist','delegation_id':task['delegation_id'],
        'contract_hash':digest(task['contract']),'max_model_calls':1,
    },[{'adoption_id':adopted['adoption_id'],'revision':1}])

    specialist=result['run']['specialists'][0]
    assert specialist['adoption_id']==adopted['adoption_id']
    assert specialist['revision']==1
    assert specialist['effective']['methods']==entry['configuration']['methods']


async def test_independent_specialist_children_get_distinct_runs_and_call_in_parallel(task,runs,ready,monkeypatch):
    from .test_ontology import command

    profiles=ready[2]
    catalog={item['source']['ref']['id']:item for item in profiles.builtin_catalog()}
    adopted={}
    for index, agent_id in enumerate(('builtin:text-writer','builtin:creative-ideator','builtin:script-editor')):
        entry=catalog[agent_id]
        adopted[agent_id]=await profiles.apply(command('adopt',{'source':'builtin',
            'ref':entry['source']['ref'],'proof':entry['proof']},f'specialist-{index}'))
    writer=adopted['builtin:text-writer']['adoption_id']
    ideation=adopted['builtin:creative-ideator']['adoption_id']
    unused=adopted['builtin:script-editor']['adoption_id']
    children=[
        {'task_key':'writer','adoption_id':writer,'target_node_ids':['a'],'input_indices':[],
         'allowed_operations':['proposal.create'],'goal':'writer task','acceptance':['写一个候选']},
        {'task_key':'ideation','adoption_id':ideation,'target_node_ids':['b'],'input_indices':[],
         'allowed_operations':['proposal.create'],'goal':'ideation task','acceptance':['写一个方向']},
    ]
    root_adoption=task['contract']['agent']['adoption_id']
    run=(await runs.start(start_request(task,max_model_calls=3,
        specialist_ids=[root_adoption,writer,ideation,unused])))['run']
    calls=[]
    child_prompts=[]
    both_children_entered=asyncio.Event()

    async def model(_ctx,_route,prompt,**identity):
        value=json.loads(prompt)
        calls.append((value,identity))
        if value['goal']==task['contract']['goal']:
            return decision(action(),delegations=children)
        child_prompts.append(value)
        if len(child_prompts)==2:
            both_children_entered.set()
        await asyncio.wait_for(both_children_entered.wait(),1)
        target='a' if value['goal']=='writer task' else 'b'
        return decision(action(title=value['goal'],text=f"来自{value['goal']}的候选",
                               target_node_ids=[target]))

    monkeypatch.setattr(agent_model,'run_decision',model)
    await asyncio.wait_for(runs.drive(run['id']),8)
    final=await runs.get(run['id'])
    assert len(calls)==3
    assert len(child_prompts)==2
    assert len({json.dumps(value['agent'],sort_keys=True) for value in child_prompts})==2
    assert final['state']=='delivered'
    assert len(final['rounds'])==1
    assert len(final['outputs'])==1
    assert len(final['children'])==2
    assert len({item['child_run_id'] for item in final['children']})==2
    assert {item['task_key'] for item in final['children']}=={'writer','ideation'}
    with runs.read() as conn:
        budget=conn.execute('SELECT used_model_calls FROM agent_run_model_budgets WHERE root_delegation_id=?',
                             (task['delegation_id'],)).fetchone()
    assert budget['used_model_calls']==3


async def test_child_start_replay_keeps_the_same_independent_run(task,runs,monkeypatch):
    from novelvideo.creation import agent_runs as run_module

    adoption=task['contract']['agent']['adoption_id']
    child={'task_key':'writer','adoption_id':adoption,'target_node_ids':['a'],'input_indices':[],
        'allowed_operations':['proposal.create'],'goal':'writer replay task','acceptance':['交付候选']}
    model_calls=[]
    async def model(_ctx,_route,prompt,**identity):
        model_calls.append(json.loads(prompt)['goal'])
        return decision(delegations=[child]) if len(model_calls)==1 else decision(action())
    monkeypatch.setattr(agent_model,'run_decision',model)
    identifier=(await runs.start(start_request(task,max_model_calls=2,
        specialist_ids=[adoption])))['run']['id']
    original_send=runs._send
    start_call={}
    crashed=False
    async def crash_after_child_start(action_name,payload,request_id=None):
        nonlocal crashed
        result=await original_send(action_name,payload,request_id)
        if action_name=='start_child' and not crashed:
            crashed=True
            start_call.update(payload=payload,request_id=request_id)
            raise asyncio.CancelledError()
        return result
    monkeypatch.setattr(runs,'_send',crash_after_child_start)
    with pytest.raises(asyncio.CancelledError):
        await runs.drive(identifier)
    parent=await runs.get(identifier)
    child_record=parent['children'][0]
    assert parent['state']=='applying' and child_record['state']=='queued'
    replay=await original_send('start_child',start_call['payload'],start_call['request_id'])
    assert replay['run']['id']==child_record['child_run_id']
    with runs.read() as conn:
        assert conn.execute('SELECT COUNT(*) FROM agent_run_children WHERE parent_run_id=?',
                            (identifier,)).fetchone()[0]==1
        assert conn.execute('SELECT COUNT(*) FROM agent_runs WHERE id=?',
                            (child_record['child_run_id'],)).fetchone()[0]==1

    monkeypatch.setattr(run_module.time,'time',lambda:10_000_000_000)
    await runs.control(identifier,'resume',{'request_id':'resume-child-start','expected_revision':parent['revision']})
    monkeypatch.setattr(run_module,'checkpoint',lambda phase:None)
    await runs.drive(identifier)
    final=await runs.get(identifier)
    assert final['state']=='delivered'
    assert final['children'][0]['child_run_id']==child_record['child_run_id']
    assert model_calls==[task['contract']['goal'],'writer replay task']


async def test_child_dependency_receives_only_a_verified_sibling_story_fragment(task,runs,monkeypatch):
    adoption=task['contract']['agent']['adoption_id']
    children=[
        {'task_key':'writer','adoption_id':adoption,'target_node_ids':['a'],'input_indices':[],
         'allowed_operations':['proposal.create'],'goal':'writer dependency task','acceptance':['交付候选'],
         'input_output_selections':[{'source_task_key':None,'output_index':0}]},
        {'task_key':'reviewer','adoption_id':adoption,'target_node_ids':['b'],'input_indices':[],
         'allowed_operations':['proposal.create'],'goal':'reviewer dependency task','acceptance':['核对候选'],
         'depends_on':['writer'],'input_output_selections':[
             {'source_task_key':'writer','output_index':0}]},
    ]
    reviewer_prompts=[]
    async def model(_ctx,_route,prompt,**identity):
        value=json.loads(prompt)
        if value['goal']==task['contract']['goal']:
            return decision(action(title='parent candidate',text='父运行已提交的真实候选。',
                                   target_node_ids=['a']),delegations=children)
        if value['goal']=='writer dependency task':
            return decision(action(title='writer candidate',text='只由 writer 写下的已提交候选。',
                                   target_node_ids=['a']))
        reviewer_prompts.append(value)
        return decision(action(title='reviewer candidate',text='审阅后的独立候选。',
                               target_node_ids=['b']))
    monkeypatch.setattr(agent_model,'run_decision',model)
    run=(await runs.start(start_request(task,max_model_calls=3,
        specialist_ids=[adoption])))['run']
    await asyncio.wait_for(runs.drive(run['id']),8)

    final=await runs.get(run['id'])
    assert final['state']=='delivered'
    assert len(final['outputs'])==1
    assert [child['state'] for child in final['children']]==['delivered','delivered']
    assert len(reviewer_prompts)==1
    selected=[item for item in reviewer_prompts[0]['inputs']
              if item['ref'].get('kind')=='run_output']
    assert len(selected)==1
    assert selected[0]['snapshot']['kind']=='story_fragment'
    assert selected[0]['snapshot']['payload']['text']=='只由 writer 写下的已提交候选。'
    provenance=selected[0]['snapshot']['provenance']
    assert provenance['source_run_id']==final['children'][0]['child_run_id']
    assert provenance['source_task_key']=='writer'
    assert provenance['source_output_id']
    with runs.read() as conn:
        writer=runs._get(conn,final['children'][0]['child_run_id'])
        parent_selected=[item for item in runs._contract(conn,writer['delegation_id'])['inputs']
                         if item['ref'].get('kind')=='run_output']
    assert len(parent_selected)==1
    assert parent_selected[0]['ref']['source_task_key'] is None
    assert parent_selected[0]['ref']['source_run_id']==run['id']


@pytest.mark.parametrize(('add_unrelated_node','writer_nested_delegation'),[
    (False,False),(True,False),(False,True),
])
async def test_writer_draft_can_be_selected_as_exact_script_document_for_reviewer(
        ctx,ready,service,task,monkeypatch,add_unrelated_node,writer_nested_delegation):
    from novelvideo.creation.agent_tools import AgentTools
    from novelvideo.chat.conversations import ConversationStore
    from novelvideo.creation.canvas_workspaces import CanvasCreate, CanvasWorkspace
    from novelvideo.creation.projection import ProjectionService
    from novelvideo.creation.store import ObjectStore
    from novelvideo.freezone.canvas_store import read_canvas
    from .test_ontology import command
    from novelvideo.creation.delegations import Delegations
    from .test_delegations import create as create_delegation
    objects=ObjectStore(ctx.state_dir)
    created=CanvasWorkspace(objects,project_id='p').create(
        'alice',CanvasCreate(request_id='script-handoff-concept',kind='concept',title='剧本交接'))
    ProjectionService(objects).apply(created.operation_id)
    canvas_id=created.object_refs[0]['canvas_id']
    conversation=ConversationStore('alice','project','p').create(
        '剧本讨论',canvas_id=canvas_id,request_id='script-handoff-conversation')
    conversation_id=conversation['id']

    body={'canvas_id':canvas_id,'conversation_id':conversation_id,
          'scope':{'kind':'canvas'},'agent':{'kind':'main'},'input_refs':[],
          'goal':'编写并审阅正式剧本','task_brief':
              '林舟30岁、短黑发、蓝灰工装；陈奶奶70岁、灰白发和栗色衣服；雨夜停电，门厅灯灭。',
          'allowed_operations':['context.read'],
          'acceptance':['提供正式剧本草稿和独立审阅']}
    formal_root=Delegations(ctx,username='alice',authority='user',
        trusted_preparation_kind='formal_episode_script')
    root_delegation=await create_delegation(formal_root,body,'script-handoff-root')
    assert root_delegation['contract']['allowed_operations']==['context.read']
    assert root_delegation['contract']['delegable_operations']==[
        'script.document.draft','script.document.review']
    profiles=ready[2]
    catalog={item['source']['ref']['id']:item for item in profiles.builtin_catalog()}
    writer_entry=catalog['builtin:text-writer']
    reviewer_entry=catalog['builtin:script-editor']
    writer_adoption=await profiles.apply(command('adopt',{'source':'builtin',
        'ref':writer_entry['source']['ref'],'proof':writer_entry['proof']},'script-handoff-writer'))
    reviewer_adoption=await profiles.apply(command('adopt',{'source':'builtin',
        'ref':reviewer_entry['source']['ref'],'proof':reviewer_entry['proof']},'script-handoff-reviewer'))
    assert writer_adoption['adoption_id']!=reviewer_adoption['adoption_id']
    specialists=[{'adoption_id':writer_adoption['adoption_id'],'revision':writer_adoption['revision']},
                 {'adoption_id':reviewer_adoption['adoption_id'],'revision':reviewer_adoption['revision']}]
    async def resolve(role):return ctx
    scoped_runs=AgentRuns(ctx,username='alice',canvas_id=canvas_id,
        conversation_id=conversation_id,resolve_context=resolve)
    monkeypatch.setattr(agent_model,'availability',lambda _: {
        'available':True,'code':'configured_not_verified','configuration_fingerprint':'config-1',
        'route':{'runtime':'model_api','model':'test','task_role':'creation_agent_decision','source':'global'}})

    children=[
        {'task_key':'writer','scope_kind':'canvas','adoption_id':writer_adoption['adoption_id'],
         'target_node_ids':[],'input_indices':[],'allowed_operations':['script.document.draft'],
         'goal':'writer screenplay task','acceptance':['交付正式剧本草稿']},
        {'task_key':'reviewer','scope_kind':'canvas','adoption_id':reviewer_adoption['adoption_id'],
         'target_node_ids':[],'input_indices':[],'allowed_operations':['script.document.review'],
         'goal':'reviewer screenplay task','acceptance':['交付独立审阅报告'],
         'depends_on':['writer'],'input_output_selections':[
             {'source_task_key':'writer','output_index':0}]},
    ]
    reviewer_prompts=[]
    writer_role_policies=[]
    writer_draft_calls=0
    model_prompts=[]
    brief='林舟30岁、短黑发、蓝灰工装；陈奶奶70岁、灰白发和栗色衣服；雨夜停电，门厅灯灭。'
    async def model(_ctx,_route,prompt,**identity):
        value=json.loads(prompt)
        model_prompts.append(value)
        if value['goal']=='编写并审阅正式剧本':
            assert value['task_brief']==brief
            assert 'frozen original user creation request' in value['operation_semantics']['task_brief']
            assert value['allowed_operations']==['context.read']
            assert value['delegation_policy']['delegable_operations']==[
                'script.document.draft','script.document.review']
            assert 'dedicated writer' in value['delegation_policy']['instruction']
            assert 'input_indices' in value['delegation_policy']['input_selection_rule']
            assert 'only parent frozen inputs' in value['delegation_policy']['input_selection_rule']
            assert value['delegation_policy']['reviewer_output_reference_example']=={
                'writer_child_field_fragment':{
                    'task_key':'draft_episode_1','input_indices':[],
                },
                'reviewer_child_field_fragment':{
                    'task_key':'review_episode_1','input_indices':[],
                    'depends_on':['draft_episode_1'],
                    'input_output_selections':[
                        {'source_task_key':'draft_episode_1','output_index':0}],
                },
            }
            assert 'do not put writer output 0 in input_indices' in value['delegation_policy']['instruction'].lower()
            return decision(delegations=children)
        if value['goal']=='writer screenplay task':
            nonlocal writer_draft_calls
            writer_role_policies.append(value.get('formal_script_role_policy'))
            assert value['task_brief']==brief
            assert 'remains binding' in value['operation_semantics']['task_brief']
            writer_draft_calls+=1
            if writer_draft_calls==1:
                if writer_nested_delegation:
                    return decision(delegations=[
                        {'task_key':'draft_episode_1','scope_kind':'canvas',
                         'adoption_id':writer_adoption['adoption_id'],'target_node_ids':[],
                         'input_indices':[],'allowed_operations':['script.document.draft'],
                         'goal':'nested draft task','acceptance':['保存正式剧本草案。']},
                        {'task_key':'review_episode_1','scope_kind':'canvas',
                         'adoption_id':reviewer_adoption['adoption_id'],'target_node_ids':[],
                         'input_indices':[],'allowed_operations':['script.document.review'],
                         'goal':'nested review task','acceptance':['审阅固定剧本草案。'],
                         'depends_on':['draft_episode_1'],
                         'input_output_selections':[{'source_task_key':'draft_episode_1','output_index':0}]},
                    ])
                return decision(action('script.document.draft',target_node_ids=[],input_indices=[],
                    acceptance_indices=[0],episode_number=1,title='第一集·雨夜来信',
                    markdown='# 第一集：雨夜来信\n\n## 1-1 雨夜·外\n林夏发现一封旧信。\n'),finished=False)
            return decision(finished=True)
        assert value['task_brief']==brief
        assert 'never expands canvas/node scope' in value['operation_semantics']['task_brief']
        reviewer_prompts.append(value)
        return decision(action('script.document.review',target_node_ids=[],input_indices=[0],
            acceptance_indices=[0],document_input_index=0,
            report={'conclusion':'passed','summary':'因果清楚，可以进入下一轮。','issues':[]}))

    monkeypatch.setattr(agent_model,'run_decision',model)
    original_execute=AgentTools.execute
    async def execute_and_maybe_add_unrelated(self,body,*,accepted_transition=None):
        result=await original_execute(self,body,accepted_transition=accepted_transition)
        if add_unrelated_node and body.get('operation')=='script.document.draft' and self.canvas_id==canvas_id:
            path=canvas_path(ctx.state_dir,canvas_id)
            graph=json.loads(path.read_text())
            graph['nodes'].append({'id':'unrelated-after-script-draft','type':'textAnnotationNode',
                'position':{'x':1400,'y':0},'data':{'text':'不属于本次任务的节点'}})
            graph['revision']+=1
            path.write_text(json.dumps(graph,ensure_ascii=False))
        return result
    monkeypatch.setattr(AgentTools,'execute',execute_and_maybe_add_unrelated)
    run=(await scoped_runs.start_with_pinned_specialists({
        'request_id':'script-handoff-run','delegation_id':root_delegation['delegation_id'],
        'contract_hash':digest(root_delegation['contract']),'max_model_calls':4,
    },specialists))['run']
    await asyncio.wait_for(scoped_runs.drive(run['id']),12)

    final=await scoped_runs.get(run['id'])
    if writer_nested_delegation:
        assert final['state']=='partial'
        assert final['children'][0]['state']=='blocked'
        assert final['children'][1]['state']=='unsatisfied'
        writer_run=await scoped_runs.get(final['children'][0]['child_run_id'])
        assert writer_run['error']['code']=='model_response_invalid'
        assert writer_run['children']==[]
        assert writer_role_policies==[{
            'role':'writer','operation':'script.document.draft',
            'instruction':'Perform script.document.draft yourself in this run. Do not delegate to another specialist or create child tasks.',
        }]
        assert writer_draft_calls==1
        assert reviewer_prompts==[]
        return
    if add_unrelated_node:
        assert final['state']=='partial'
        assert final['children'][0]['state']=='blocked'
        assert final['children'][1]['state']=='unsatisfied'
        assert writer_draft_calls==1
        assert reviewer_prompts==[]
        writer_run=await scoped_runs.get(final['children'][0]['child_run_id'])
        assert writer_run['error']['code']=='stale_input'
        return
    assert final['state']=='delivered',(final['state'],final.get('error'),final.get('unmet'),
        final.get('unmet_by_task'),final['children'],
        [(item.get('goal'),item.get('allowed_operations'),item.get('delegation_policy'))
         for item in model_prompts])
    assert [child['state'] for child in final['children']]==['delivered','delivered']
    assert writer_draft_calls==2
    assert len(reviewer_prompts)==1
    assert writer_role_policies==[{
        'role':'writer','operation':'script.document.draft',
        'instruction':'Perform script.document.draft yourself in this run. Do not delegate to another specialist or create child tasks.',
    }]*2
    assert reviewer_prompts[0]['formal_script_role_policy']=={
        'role':'reviewer','operation':'script.document.review',
        'instruction':'Perform script.document.review yourself in this run. Do not delegate to another specialist or create child tasks.',
    }
    selected=[item for item in reviewer_prompts[0]['inputs']
              if item['ref'].get('kind')=='script_document']
    assert len(selected)==1
    ref=selected[0]['ref']
    assert ref['canvas_id']==canvas_id
    assert selected[0]['snapshot']['kind']=='episode_script'
    assert selected[0]['snapshot']['current_revision_id']==ref['revision_id']
    assert selected[0]['snapshot']['markdown'].startswith('# 第一集：雨夜来信')
    writer_run=await scoped_runs.get(final['children'][0]['child_run_id'])
    projection=next(output['projection'] for output in writer_run['outputs']
                    if output['operation']=='script.document.draft')
    graph=read_canvas(ctx.state_dir,canvas_id)
    node=next(item for item in graph['nodes'] if item['id']==projection['node_id'])
    assert node['data']['formalScriptRef']['document_id']==ref['document_id']
    assert node['data']['formalScriptRef']['revision_id']==ref['revision_id']


async def test_stopping_parent_cascades_to_started_children(task,runs,monkeypatch):
    adoption=task['contract']['agent']['adoption_id']
    children=[
        {'task_key':'first','adoption_id':adoption,'target_node_ids':['a'],'input_indices':[],
         'allowed_operations':['proposal.create'],'goal':'first slow task','acceptance':['候选']},
        {'task_key':'second','adoption_id':adoption,'target_node_ids':['b'],'input_indices':[],
         'allowed_operations':['proposal.create'],'goal':'second slow task','acceptance':['候选']},
    ]
    children_entered=asyncio.Event()
    release=asyncio.Event()
    entered=[]
    async def model(_ctx,_route,prompt,**identity):
        goal=json.loads(prompt)['goal']
        if goal==task['contract']['goal']:
            return decision(delegations=children)
        entered.append(goal)
        if len(entered)==2:
            children_entered.set()
        await release.wait()
        return decision(action())
    monkeypatch.setattr(agent_model,'run_decision',model)
    identifier=(await runs.start(start_request(task,max_model_calls=3,
        specialist_ids=[adoption])))['run']['id']
    work=asyncio.create_task(runs.drive(identifier))
    await asyncio.wait_for(children_entered.wait(),4)
    waiting=await runs.get(identifier)
    assert waiting['state']=='waiting_children'

    await runs.control(identifier,'stop',{'request_id':'stop-parent-with-children',
        'expected_revision':waiting['revision']})
    release.set()
    await asyncio.wait_for(work,4)
    stopped=await runs.get(identifier)
    assert stopped['state']=='stopped'
    assert len(stopped['children'])==2
    assert all(item['state']=='stopped' for item in stopped['children'])
    children_after_stop=[await runs.get(item['child_run_id']) for item in stopped['children']]
    assert all(item['rounds'][0]['phase']=='submission_unknown' for item in children_after_stop)


async def test_parent_stop_committed_before_child_action_fences_write(task,runs,monkeypatch):
    from novelvideo.creation.agent_runs import _RunTools

    adoption=task['contract']['agent']['adoption_id']
    child={'task_key':'writer','adoption_id':adoption,'target_node_ids':['a'],'input_indices':[],
        'allowed_operations':['proposal.create'],'goal':'write after parent stop','acceptance':['候选']}
    action_entered=asyncio.Event()
    release_action=asyncio.Event()
    stop_committed=asyncio.Event()
    release_cascade=asyncio.Event()
    model_calls=[]
    async def model(_ctx,_route,prompt,**identity):
        goal=json.loads(prompt)['goal']
        model_calls.append(goal)
        if goal==task['contract']['goal']:
            return decision(delegations=[child])
        return decision(action(title='不应写入',text='父停止之后的候选。',target_node_ids=['a']))
    monkeypatch.setattr(agent_model,'run_decision',model)
    identifier=(await runs.start(start_request(task,max_model_calls=2,
        specialist_ids=[adoption])))['run']['id']
    original_execute=_RunTools.execute
    async def pause_child_action(self,body,*,accepted_transition=None):
        if self.identifier != identifier:
            action_entered.set()
            await release_action.wait()
        return await original_execute(self,body,accepted_transition=accepted_transition)
    monkeypatch.setattr(_RunTools,'execute',pause_child_action)
    work=asyncio.create_task(runs.drive(identifier))
    await asyncio.wait_for(action_entered.wait(),4)
    waiting=await runs.get(identifier)
    assert waiting['state']=='waiting_children'
    child_row=waiting['children'][0]

    original_cascade=runs._stop_child_runs
    async def pause_cascade(value):
        stop_committed.set()
        await release_cascade.wait()
        await original_cascade(value)
    monkeypatch.setattr(runs,'_stop_child_runs',pause_cascade)
    stopping=asyncio.create_task(runs.control(identifier,'stop',{'request_id':'stop-before-child-write',
        'expected_revision':waiting['revision']}))
    await asyncio.wait_for(stop_committed.wait(),4)
    release_action.set()
    await asyncio.wait_for(work,4)
    with runs.read() as conn:
        table=conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='delegation_outputs'").fetchone()
        outputs=(conn.execute('SELECT COUNT(*) FROM delegation_outputs WHERE delegation_id=?',
                              (child_row['delegation_id'],)).fetchone()[0] if table else 0)
    assert outputs==0
    assert model_calls==[task['contract']['goal'],'write after parent stop']
    release_cascade.set()
    await asyncio.wait_for(stopping,4)
    assert (await runs.get(child_row['child_run_id']))['state']=='stopped'


async def test_parent_stop_preserves_child_model_unknown_and_parent_unmet(task,runs,monkeypatch):
    adoption=task['contract']['agent']['adoption_id']
    child={'task_key':'writer','adoption_id':adoption,'target_node_ids':['a'],'input_indices':[],
        'allowed_operations':['proposal.create'],'goal':'unknown child task','acceptance':['候选']}
    async def model(_ctx,_route,prompt,**identity):
        if json.loads(prompt)['goal']==task['contract']['goal']:
            return decision(delegations=[child])
        raise TimeoutError('provider outcome unknown')
    monkeypatch.setattr(agent_model,'run_decision',model)
    identifier=(await runs.start(start_request(task,max_model_calls=2,
        specialist_ids=[adoption])))['run']['id']
    await runs.drive(identifier)
    parent=await runs.get(identifier)
    child_id=parent['children'][0]['child_run_id']
    unknown=await runs.get(child_id)
    assert parent['state']=='partial' and parent['unmet_by_task']['0:writer']
    assert unknown['state']=='submission_unknown'

    await runs.control(identifier,'stop',{'request_id':'stop-parent-after-unknown',
        'expected_revision':parent['revision']})
    preserved=await runs.get(child_id)
    assert preserved['state']=='submission_unknown'
    with pytest.raises(OperationError):
        await runs.control(child_id,'resume',{'request_id':'resume-unknown-child',
            'expected_revision':preserved['revision']})


async def test_empty_canvas_can_delegate_reversible_preparation(ready,service,runs,monkeypatch):
    from .test_delegations import create,selection

    canvas_id,_,agents,_=ready
    path=canvas_path(runs.ctx.state_dir,canvas_id)
    graph=json.loads(path.read_text())
    graph['nodes']=[]
    graph['revision']+=1
    path.write_text(json.dumps(graph))
    parent=await create(service,selection(ready,scope={'kind':'canvas'},
        allowed_operations=['context.read','proposal.create']), 'empty-canvas-parent')
    adoption=parent['contract']['agent']['adoption_id']
    child={'task_key':'designer','scope_kind':'canvas','adoption_id':adoption,
        'target_node_ids':[],'input_indices':[],'allowed_operations':['proposal.create'],
        'goal':'空白画布的设计方向','acceptance':['提供一个文字候选']}
    prompts=[]
    async def model(_ctx,_route,prompt,**identity):
        value=json.loads(prompt)
        prompts.append(value)
        if value['goal']==parent['contract']['goal']:
            return decision(delegations=[child])
        return decision(action(title='空白画布方向',text='先确定故事的情绪与冲突。',
                               target_node_ids=[]))
    monkeypatch.setattr(agent_model,'run_decision',model)
    run=(await runs.start(start_request(parent,max_model_calls=2,
        specialist_ids=[adoption])))['run']
    await asyncio.wait_for(runs.drive(run['id']),8)

    final=await runs.get(run['id'])
    assert final['state']=='delivered'
    assert final['outputs']==[]
    assert final['children'][0]['state']=='delivered'
    assert len(prompts)==2 and prompts[1]['nodes']==[]
    with runs.read() as conn:
        child_run=runs._get(conn,final['children'][0]['child_run_id'])
        child_contract=runs._contract(conn,child_run['delegation_id'])
    assert child_contract['expanded_scope']['scope']['kind']=='canvas'
    assert child_contract['expanded_scope']['node_ids']==[]
    assert child_contract['allowed_operations']==['proposal.create']


async def test_local_parent_cannot_expand_child_to_canvas(task,runs,monkeypatch):
    adoption=task['contract']['agent']['adoption_id']
    child={'task_key':'designer','scope_kind':'canvas','adoption_id':adoption,
        'target_node_ids':[],'input_indices':[],'allowed_operations':['node.run'],
        'goal':'越界画布任务','acceptance':['交付结果']}
    async def model(_ctx,_route,prompt,**identity):
        value=json.loads(prompt)
        if value['goal']==task['contract']['goal']:
            return decision(action(target_node_ids=['a']),delegations=[child])
        pytest.fail('拒绝的局部扩张不得创建 child run 或调用模型')
    monkeypatch.setattr(agent_model,'run_decision',model)
    run=(await runs.start(start_request(task,max_model_calls=2,
        specialist_ids=[adoption])))['run']
    await runs.drive(run['id'])
    final=await runs.get(run['id'])

    assert final['state']=='partial',(final['children'],final['unmet'],final.get('unmet_by_task'))
    assert final['children'][0]['state']=='blocked'
    assert final['children'][0]['child_run_id'] is None
    assert final['unmet_by_task']['0:designer']


async def test_empty_canvas_rejects_node_run_even_for_run_enabled_specialist(ready,service,runs,monkeypatch):
    from .test_delegations import create,selection
    from .test_ontology import command

    canvas_id,_,profiles,_=ready
    path=canvas_path(runs.ctx.state_dir,canvas_id)
    graph=json.loads(path.read_text())
    graph['nodes']=[]
    graph['revision']+=1
    path.write_text(json.dumps(graph))
    entry=next(item for item in profiles.builtin_catalog()
               if item['source']['ref']['id']=='builtin:creative-ideator')
    adopted=await profiles.apply(command('adopt',{'source':'builtin',
        'ref':entry['source']['ref'],'proof':entry['proof']},'empty-canvas-runner'))
    parent=await create(service,selection(ready,scope={'kind':'canvas'},
        agent={'kind':'adoption','adoption_id':adopted['adoption_id'],'revision':adopted['revision']},
        allowed_operations=['context.read','proposal.create','node.run']), 'empty-canvas-node-run-parent')
    child={'task_key':'runner','scope_kind':'canvas','adoption_id':adopted['adoption_id'],
        'target_node_ids':[],'input_indices':[],'allowed_operations':['node.run'],
        'goal':'在空画布运行节点','acceptance':['交付运行结果']}
    async def model(_ctx,_route,prompt,**identity):
        if json.loads(prompt)['goal']==parent['contract']['goal']:
            return decision(delegations=[child])
        pytest.fail('空画布拒绝 node.run 后不得创建 child run 或发起模型请求')
    monkeypatch.setattr(agent_model,'run_decision',model)
    root=(await runs.start(start_request(parent,max_model_calls=2,
        specialist_ids=[adopted['adoption_id']])))['run']
    await runs.drive(root['id'])
    final=await runs.get(root['id'])

    assert final['state']=='partial'
    assert final['children'][0]['state']=='blocked'
    assert final['children'][0]['child_run_id'] is None
    assert 'capability_denied' in final['unmet_by_task']['0:runner'][0]


async def test_prior_failed_child_survives_later_parent_round_with_same_task_key(task,runs,monkeypatch):
    adoption=task['contract']['agent']['adoption_id']
    blocked={'task_key':'designer','scope_kind':'canvas','adoption_id':adoption,
        'target_node_ids':[],'input_indices':[],'allowed_operations':['proposal.create'],
        'goal':'局部任务不能扩大画布','acceptance':['候选']}
    recovered={'task_key':'designer','adoption_id':adoption,'target_node_ids':['b'],'input_indices':[],
        'allowed_operations':['proposal.create'],'goal':'第二轮重新准备','acceptance':['第二个候选']}
    root_calls=0
    async def model(_ctx,_route,prompt,**identity):
        nonlocal root_calls
        goal=json.loads(prompt)['goal']
        if goal==task['contract']['goal']:
            root_calls+=1
            if root_calls==1:
                return decision(action(target_node_ids=['a']),finished=False,
                    unmet=['主创仍需第二轮'],delegations=[blocked])
            return decision(action(target_node_ids=['b']),delegations=[recovered])
        return decision(action(target_node_ids=['b']))
    monkeypatch.setattr(agent_model,'run_decision',model)
    identifier=(await runs.start(start_request(task,max_model_calls=3,
        specialist_ids=[adoption])))['run']['id']
    await asyncio.wait_for(runs.drive(identifier),8)

    final=await runs.get(identifier)
    assert final['state']=='partial'
    assert len(final['rounds'])==2
    assert [(child['round_index'],child['task_key'],child['state']) for child in final['children']]==[
        (0,'designer','blocked'),(1,'designer','delivered')]
    assert final['unmet_by_task']['0:designer']
    assert '1:designer' not in final['unmet_by_task']
    assert final['unmet'] and any('启动失败' in item for item in final['unmet'])


async def test_stopped_dependency_waits_for_explicit_child_and_parent_resume(task,runs,monkeypatch):
    adoption=task['contract']['agent']['adoption_id']
    children=[
        {'task_key':'producer','adoption_id':adoption,'target_node_ids':['a'],'input_indices':[],
         'allowed_operations':['proposal.create'],'goal':'slow prerequisite','acceptance':['候选']},
        {'task_key':'consumer','adoption_id':adoption,'target_node_ids':['b'],'input_indices':[],
         'allowed_operations':['proposal.create'],'goal':'dependent task','acceptance':['候选'],
         'depends_on':['producer'],'input_output_selections':[
             {'source_task_key':'producer','output_index':0}]},
    ]
    child_drive_entered=asyncio.Event()
    release_child_drive=asyncio.Event()
    stop_committed=asyncio.Event()
    release_cascade=asyncio.Event()
    model_calls=[]
    async def model(_ctx,_route,prompt,**identity):
        goal=json.loads(prompt)['goal']
        model_calls.append(goal)
        if goal==task['contract']['goal']:
            return decision(delegations=children)
        target='a' if goal=='slow prerequisite' else 'b'
        return decision(action(title=f'{goal} 候选',text=f'{goal} 已完成。',target_node_ids=[target]))
    monkeypatch.setattr(agent_model,'run_decision',model)
    identifier=(await runs.start(start_request(task,max_model_calls=3,
        specialist_ids=[adoption])))['run']['id']
    original_drive=runs.drive
    async def hold_child_drive(run_id):
        if run_id != identifier:
            child_drive_entered.set()
            await release_child_drive.wait()
        return await original_drive(run_id)
    monkeypatch.setattr(runs,'drive',hold_child_drive)
    work=asyncio.create_task(original_drive(identifier))
    await asyncio.wait_for(child_drive_entered.wait(),4)
    waiting=await runs.get(identifier)
    assert waiting['state']=='waiting_children'

    original_cascade=runs._stop_child_runs
    async def pause_cascade(value):
        stop_committed.set()
        await release_cascade.wait()
        await original_cascade(value)
    monkeypatch.setattr(runs,'_stop_child_runs',pause_cascade)
    stopping=asyncio.create_task(runs.control(identifier,'stop',{'request_id':'stop-dependent-parent',
        'expected_revision':waiting['revision']}))
    await asyncio.wait_for(stop_committed.wait(),4)
    producer_id=(await runs.get(identifier))['children'][0]['child_run_id']
    # The parent stop is committed, while asynchronous cascade is paused.
    # A child claim must check its ancestor fence and make no model call.
    await asyncio.wait_for(original_drive(producer_id),4)
    assert (await runs.get(producer_id))['state']=='queued'
    assert model_calls==[task['contract']['goal']]
    release_cascade.set()
    await asyncio.wait_for(stopping,4)
    release_child_drive.set()
    await asyncio.wait_for(work,4)
    stopped=await runs.get(identifier)
    assert stopped['state']=='stopped'
    assert stopped['children'][1]['state']=='planned'
    producer_id=stopped['children'][0]['child_run_id']
    assert (await runs.get(producer_id))['state']=='stopped'

    # Explicit parent resume observes the stopped prerequisite and exits in a
    # bounded waiting state. It does not silently resume that child.
    await runs.control(identifier,'resume',{'request_id':'resume-dependent-parent-once',
        'expected_revision':stopped['revision']})
    await asyncio.wait_for(original_drive(identifier),4)
    waiting_again=await runs.get(identifier)
    assert waiting_again['state']=='waiting_children'
    assert waiting_again['children'][1]['state']=='planned'
    assert model_calls==[task['contract']['goal']]

    # User explicitly resumes the prerequisite, then explicitly resumes the
    # parent to reevaluate dependencies and start the consumer.
    producer=await runs.get(producer_id)
    await runs.control(producer_id,'resume',{'request_id':'resume-producer',
        'expected_revision':producer['revision']})
    await asyncio.wait_for(original_drive(producer_id),4)
    producer_done=await runs.get(producer_id)
    assert producer_done['state']=='delivered'
    await runs.control(identifier,'resume',{'request_id':'resume-dependent-parent-twice',
        'expected_revision':waiting_again['revision']})
    await asyncio.wait_for(original_drive(identifier),4)
    final=await runs.get(identifier)
    assert final['state']=='delivered',(final['state'],final.get('unmet'),final.get('unmet_by_task'),final['children'])
    assert [child['state'] for child in final['children']]==['delivered','delivered']
    assert model_calls==[task['contract']['goal'],'slow prerequisite','dependent task']


async def test_stopped_grandchild_wait_exits_and_resumes_through_ancestors(task,runs,monkeypatch):
    adoption=task['contract']['agent']['adoption_id']
    grandchild={'task_key':'specialist-c','adoption_id':adoption,'target_node_ids':['a'],
        'input_indices':[],'allowed_operations':['proposal.create'],'goal':'deep specialist C',
        'acceptance':['候选']}
    child={'task_key':'specialist-b','adoption_id':adoption,'target_node_ids':['a'],
        'input_indices':[],'allowed_operations':['proposal.create'],'goal':'middle specialist B',
        'acceptance':['候选']}
    model_calls=[]
    async def model(_ctx,_route,prompt,**identity):
        goal=json.loads(prompt)['goal']
        model_calls.append(goal)
        if goal==task['contract']['goal']:
            return decision(delegations=[child])
        if goal=='middle specialist B':
            return decision(action(title='middle candidate',text='B 的已提交候选。',
                                   target_node_ids=['a']),delegations=[grandchild])
        return decision(action(title=f'{goal} 候选',text=f'{goal} 已完成。',target_node_ids=['a']))
    monkeypatch.setattr(agent_model,'run_decision',model)
    identifier=(await runs.start(start_request(task,max_model_calls=3,
        specialist_ids=[adoption])))['run']['id']
    original_drive=runs.drive
    grandchild_entered=asyncio.Event()
    release_grandchild=asyncio.Event()
    def depth(run_id):
        value=runs._read_value(run_id,raw=True)
        result=0
        while value.get('parent_run_id'):
            result+=1
            value=runs._read_value(value['parent_run_id'],raw=True)
        return result
    async def hold_grandchild(run_id):
        if depth(run_id)==2:
            grandchild_entered.set()
            await release_grandchild.wait()
        return await original_drive(run_id)
    monkeypatch.setattr(runs,'drive',hold_grandchild)
    work=asyncio.create_task(original_drive(identifier))
    await asyncio.wait_for(grandchild_entered.wait(),4)
    parent=await runs.get(identifier)
    middle_id=parent['children'][0]['child_run_id']
    middle=await runs.get(middle_id)
    grandchild_id=middle['children'][0]['child_run_id']
    assert parent['state']==middle['state']=='waiting_children'

    grandchild_state=await runs.get(grandchild_id)
    await runs.control(grandchild_id,'stop',{'request_id':'stop-deep-child',
        'expected_revision':grandchild_state['revision']})
    release_grandchild.set()
    # C is stopped; B must wait, and A must notice B made no durable progress
    # instead of repeatedly reclaiming and immediately redriving it.
    await asyncio.wait_for(work,4)
    parent=await runs.get(identifier)
    middle=await runs.get(middle_id)
    assert parent['state']=='waiting_children'
    assert middle['state']=='waiting_children'
    assert (await runs.get(grandchild_id))['state']=='stopped'
    assert model_calls==[task['contract']['goal'],'middle specialist B']

    deep=await runs.get(grandchild_id)
    await runs.control(grandchild_id,'resume',{'request_id':'resume-deep-child',
        'expected_revision':deep['revision']})
    await asyncio.wait_for(original_drive(grandchild_id),4)
    deep=await runs.get(grandchild_id)
    assert deep['state']=='delivered'
    await runs.control(middle_id,'resume',{'request_id':'resume-middle-after-deep',
        'expected_revision':middle['revision']})
    await asyncio.wait_for(original_drive(middle_id),4)
    middle=await runs.get(middle_id)
    assert middle['state']=='delivered'
    await runs.control(identifier,'resume',{'request_id':'resume-root-after-middle',
        'expected_revision':parent['revision']})
    await asyncio.wait_for(original_drive(identifier),4)
    final=await runs.get(identifier)
    assert final['state']=='delivered',(final['state'],final.get('unmet'),final.get('unmet_by_task'),final['children'])
    assert model_calls==[task['contract']['goal'],'middle specialist B','deep specialist C']


async def test_legacy_rounds_migrate_once_and_keep_unknown_calls(ctx,ready,task,runs,monkeypatch):
    from novelvideo.creation.store import ObjectStore
    from novelvideo.creation.library_references import packed

    root=(await runs.start(start_request(task,max_model_calls=2)))['run']
    children=await _create_child_delegations(ctx,ready,task,count=3)
    child_runs=[(await runs.start(_start_for_delegation(child,f'legacy-child-{i}',max_model_calls=24)))['run']
                for i,child in enumerate(children[:2])]
    root_rounds=[{'attempt_id':'shared-legacy-attempt','phase':'submission_unknown'},
                 {'attempt_id':'proven-unsent','phase':'model_inflight','send_admitted':False},
                 {'attempt_id':'received-despite-flag','phase':'decision_saved','send_admitted':False}]
    child_rounds=[{'attempt_id':'shared-legacy-attempt','phase':'applied'},
                  {'attempt_id':'legacy-unknown','phase':'submission_unknown'}]
    store=ObjectStore(ctx.state_dir)
    with store.transaction() as conn:
        root_value=json.loads(conn.execute('SELECT content FROM agent_runs WHERE id=?',(root['id'],)).fetchone()[0])
        root_value['rounds']=root_rounds
        conn.execute('UPDATE agent_runs SET content=? WHERE id=?',(packed(root_value),root['id']))
        for run in child_runs:
            child_value=json.loads(conn.execute('SELECT content FROM agent_runs WHERE id=?',(run['id'],)).fetchone()[0])
            child_value['rounds']=child_rounds
            conn.execute('UPDATE agent_runs SET content=? WHERE id=?',(packed(child_value),run['id']))
        conn.execute('DELETE FROM agent_run_model_attempts')
        conn.execute('DELETE FROM agent_run_model_budgets')

    await runs._send('claim',{'id':root['id'],'owner':'migrate-legacy'})
    migrated=await runs.get(child_runs[0]['id'])
    with runs.read() as conn:
        budget=conn.execute('SELECT * FROM agent_run_model_budgets WHERE root_delegation_id=?',
                             (task['delegation_id'],)).fetchone()

    assert budget['max_model_calls']==2
    assert budget['used_model_calls']==3
    assert budget['legacy_used_calls']==3
    assert migrated['remaining_model_calls']==0
    monkeypatch.setattr(agent_model,'run_decision',lambda *args,**kwargs:pytest.fail('legacy overage must not send'))
    new_child=(await runs.start(_start_for_delegation(children[2],'legacy-budget-exhausted',max_model_calls=24)))['run']
    await runs.drive(new_child['id'])
    assert (await runs.get(new_child['id']))['state']=='partial'


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


async def test_task_skill_context_is_frozen_into_model_prompt_and_public_receipt(
        ctx, ready, service, runs, monkeypatch):
    agents = ready[2]
    entry = next(item for item in agents.builtin_catalog()
                 if item['id'] == 'builtin:text-writer')
    adopted = await agents.apply(command('adopt', {
        'source': 'builtin', 'ref': entry['source']['ref'], 'proof': entry['proof'],
    }, 'skill-writer-adopt'))
    script_task = await create_delegation(service, delegation_selection(
        ready,
        agent={'kind': 'adoption', 'adoption_id': adopted['adoption_id'], 'revision': 1},
        scope={'kind': 'canvas'},
        goal='为《Last Lamp》第一集提出正式剧本草稿',
        allowed_operations=['script.document.draft'],
        acceptance=['交付第一集完整剧本草稿'],
    ), 'skill-writer-task')
    prompts = []

    async def model(ctx, route, prompt, **identity):
        prompts.append(json.loads(prompt))
        return decision()

    monkeypatch.setattr(agent_model, 'run_decision', model)
    run = (await runs.start(start_request(script_task, request_id='skill-writer-start')))['run']
    await runs.drive(run['id'])
    result = await runs.get(run['id'])

    assert prompts, (result.get('state'), result.get('error'), result.get('rounds'))
    skill_context = prompts[0]['task_skill_context']
    assert [skill['id'] for skill in skill_context['skills']] == ['script-master']
    assert '当前用户请求和冻结的委托合同优先' in skill_context['precedence']
    assert [entry['id'] for entry in result['rounds'][0]['skill_context']] == ['script-master']
    skill = skill_context['skills'][0]
    assert [document['path'] for document in skill['documents']] == [
        'skills/script-master/SKILL.md',
        'skills/script-master/references/writing.md',
    ]
    expected_sections = {
        'skills/script-master/SKILL.md': [
            '第一部分 总体原则', 'S4 剧本初稿',
        ],
        'skills/script-master/references/writing.md': [
            '核心原则速记（开头冗余 1/3）',
            '第三部分 写作模式 + 分集开关',
            '第四部分 B 编剧内核六维',
            '第五部分 模式 × 节奏 × 分集开关 推荐组合矩阵',
            '第七部分 输出格式与创作笔记',
            '附录 C：中文短剧拍摄稿格式（推荐给短剧 / 短视频 / 微电影）',
            '附录 D：AI 视频配音友好的标点与发声设计',
            '附录 E：剧本转视频提示词的前置接口规范',
        ],
    }
    expected_receipt = []
    for document in skill['documents']:
        raw = (Path(__file__).resolve().parents[2] / document['path']).read_bytes()
        assert document['sha256'] == sha256(raw).hexdigest()
        assert document['selection']['mode'] == 'sections'
        section_ids = expected_sections[document['path']]
        assert document['selection']['section_ids'] == section_ids
        assert 'content' not in document
        excerpt_blocks = document['excerpt_blocks']
        assert [block['section_id'] for block in excerpt_blocks] == section_ids
        for block in excerpt_blocks:
            excerpt_bytes = block['content'].encode('utf-8')
            assert block['content'].startswith('#')
            assert excerpt_bytes in raw
            assert block['content_sha256'] == sha256(excerpt_bytes).hexdigest()
        selected_bytes = '\n\n'.join(
            block['content'] for block in excerpt_blocks
        ).encode('utf-8')
        selection = document['selection']
        assert selection['content_sha256'] == sha256(selected_bytes).hexdigest()
        expected_receipt.append({
            'path': document['path'],
            'sha256': sha256(raw).hexdigest(),
            'selection': selection,
        })
    assert result['rounds'][0]['skill_context'] == [{
        'id': 'script-master', 'documents': expected_receipt,
    }]


async def test_decision_prompt_has_operation_specific_exact_payload_contracts(ctx,ready,task,runs,monkeypatch):
    prompts=[]
    async def model(ctx,route,prompt,**identity):
        prompts.append(json.loads(prompt))
        return decision(action())
    monkeypatch.setattr(agent_model,'run_decision',model)

    run=(await runs.start(start_request(task)))['run']
    await runs.drive(run['id'])

    contract=prompts[0]['payload_contract']
    assert 'common' not in contract
    assert set(contract['context.read']) == {'target_node_ids','input_indices','acceptance_indices'}
    assert set(contract['proposal.create']) == {'target_node_ids','input_indices','acceptance_indices','title','text'}
    assert 'node.run' not in contract


def test_node_payload_contract_does_not_inherit_preparation_selection_fields():
    from novelvideo.creation.agent_runs import _payload_contract

    contract=_payload_contract({'allowed_node_ids':['frame-node'],
        'allowed_node_operations':[{'node_id':'frame-node','operation':'render'}], 'max_node_runs':1})

    assert set(contract['node.patch']) == {'node_id','expected_revision','changes'}
    assert set(contract['node.run']) == {'node_id','operation','expected_revision'}
    assert not {'target_node_ids','input_indices','acceptance_indices'} & set(contract['node.run'])


def test_asset_design_payload_contract_pins_group_node_or_image_source_target():
    from novelvideo.creation.agent_runs import _payload_contract
    from novelvideo.creation.agent_runs import AgentRuns

    contract = _payload_contract()['asset.design.create']
    target = contract['target_node_ids']
    assert 'canvas scope' in target and '[]' in target
    assert 'group scope' in target and '[group_id]' in target
    assert 'nodes scope' in target
    assert 'groupNode' in target and '[group_node_id]' in target
    assert all(node_type in target for node_type in ('imageGenNode', 'imageNode', 'uploadNode'))
    assert 'exactly one selected image source node ID' in target
    assert 'image-only' in target
    assert 'parentId' in target and 'membership' in target
    assert 'null parent' in target and 'top-level add-only' in target
    assert 'input_indices' in contract and 'reference_indices' in contract
    assert 'selected source image' in contract['reference_indices']
    assert 'only when the nodes target is an image source' in contract['reference_indices']
    assert not {'output_group_id', 'output_parent_id', 'container_node_id'} & set(contract)

    semantics = AgentRuns._operation_semantics()['asset.design.create']
    assert 'groupNode, use target_node_ids=[group_node_id]' in semantics
    assert 'A groupNode target does not require an image input reference' in semantics
    assert 'target_node_ids=[source_node_id]' in semantics
    assert 'frozen canvas_node input' in semantics
    assert 'parentId/ancestry and membership' in semantics
    assert 'null parent, the design is top-level add-only' in semantics
    assert 'Do not provide an output container ID' in semantics


def test_asset_design_payload_contract_exposes_camera_selection_and_full_body_purposes():
    from novelvideo.creation.agent_runs import _payload_contract

    contract = _payload_contract()['asset.design.create']

    assert all(value in contract['purpose'] for value in (
        'avatar', 'half_body', 'full_body', 'body', 'character_sheet', 'scene'))
    camera = contract['camera_selection']
    assert 'optional partial object' in camera
    assert all(field in camera for field in ('cameraBodyId', 'lensId', 'focalLengthMm', 'aperture'))
    assert 'arri_alexa_65' in camera and 'arri_signature_prime' in camera
    assert all(value in camera for value in ('8', '14', '24', '35', '50', '75', '125'))
    assert all(value in camera for value in ('f/1.4', 'f/2', 'f/2.8', 'f/4', 'f/5.6', 'f/8'))


def test_character_sheet_guidance_defaults_to_one_consistent_four_view_design():
    from novelvideo.creation.agent_runs import AgentRuns

    guidance = AgentRuns._operation_semantics()['asset.design.create']

    assert 'default purpose=character_sheet' in guidance
    assert 'one 16:9 character sheet image' in guidance
    assert 'large face close-up' in guidance
    assert 'full-body front, side/profile, and back views' in guidance
    assert 'same person' in guidance and 'identical face, age, hairstyle, clothing' in guidance
    assert 'head to shoes without cropping' in guidance
    assert 'when the user does not specify these settings' in guidance
    assert 'preserve the explicitly requested aspect_ratio and camera_selection' in guidance
    assert 'do not set aspect_ratio or camera_selection' not in guidance
    assert 'no text, caption, logo, or watermark' in guidance


async def test_safe_codex_diagnostic_is_persisted_without_prompt_or_output(ctx,ready,task,runs,monkeypatch):
    from novelvideo.creation.operations import OperationError
    private_prompt='private prompt contents'
    private_output='private CLI output'
    diagnostic={'stage':'codex_wire_output','code':'codex_output_invalid',
                'exception_type':'KnowledgeRuntimeError',
                'issues':[{'loc':['actions',0,'payload_json'],'type':'string_type'}]}
    async def invalid_model(*args,**kwargs):
        raise OperationError('model_response_invalid','模型未返回有效结果',response_received=True,
                             diagnostic=diagnostic)
    monkeypatch.setattr(agent_model,'run_decision',invalid_model)

    run=(await runs.start(start_request(task)))['run']
    await runs.drive(run['id'])
    result=await runs.get(run['id'])

    assert result['state']=='blocked'
    assert result['error']['diagnostic']==diagnostic
    assert not result['outputs'] and not result['rounds'][0].get('prompt')
    serialized=json.dumps(result)
    assert all(secret not in serialized for secret in (private_prompt,private_output))


async def test_agent_run_scope_is_checked_from_sql_row(task,runs):
    started=await runs.start(start_request(task))
    runs.canvas_id='different-canvas'
    with pytest.raises(OperationError) as failure:
        await runs.get(started['run']['id'])
    assert failure.value.code=='scope_violation'


async def test_model_call_budget_allows_bounded_crew_not_unlimited(task,runs):
    over_limit = start_request(task, request_id='over-limit', max_model_calls=25)
    with pytest.raises(OperationError):
        await runs.start(over_limit)
    oversized_roster = start_request(task, request_id='oversized-roster',
        specialist_ids=[str(index) for index in range(19)])
    with pytest.raises(OperationError):
        await runs.start(oversized_roster)
    allowed = await runs.start(start_request(task, request_id='bounded-crew', max_model_calls=24))
    assert allowed['run']['max_model_calls'] == 24


async def test_invalid_model_response_resumes_as_a_new_bounded_round(task,runs,monkeypatch):
    from novelvideo.creation.operations import OperationError

    calls=[]
    async def model(*a,**k):
        calls.append(1)
        if len(calls)==1:
            raise OperationError('model_response_invalid','模型未返回有效结果',response_received=True,
                diagnostic={'stage':'decision_payload','code':'decision_contract_invalid',
                            'issues':[{'loc':['actions',0],'type':'asset_design_prompt_invalid'}]})
        return decision(action())

    monkeypatch.setattr(agent_model,'run_decision',model)
    identifier=(await runs.start(start_request(task,request_id='invalid-response-resume',
                                               max_model_calls=3)))['run']['id']
    await runs.drive(identifier)
    blocked=await runs.get(identifier)
    assert blocked['state']=='blocked' and blocked['rounds'][-1]['phase']=='invalid_response'
    resumed=await runs.control(identifier,'resume',{'request_id':'resume-invalid-response',
                                                   'expected_revision':blocked['revision']})
    assert resumed['run']['state']=='queued'
    await runs.drive(identifier)
    final=await runs.get(identifier)
    assert final['state']=='delivered' and len(final['rounds'])==2 and calls==[1,1]
    assert final['rounds'][0]['phase']=='invalid_response'
    assert final['remaining_model_calls']==1


async def test_decision_validation_failure_after_received_response_is_not_unknown(task,runs,monkeypatch):
    calls=[]
    async def model(*args,**identity):
        calls.append(identity['attempt_id'])
        return decision()

    original_persist=runs._persist_child_plans
    fail_once=True
    def fail_first_persist(conn,value,round_index,delegations):
        nonlocal fail_once
        if fail_once:
            fail_once=False
            raise OperationError('formal_script_specialists_missing','固定专业成员能力不足')
        return original_persist(conn,value,round_index,delegations)

    monkeypatch.setattr(agent_model,'run_decision',model)
    monkeypatch.setattr(runs,'_persist_child_plans',fail_first_persist)
    identifier=(await runs.start(start_request(task,request_id='received-response-validation',
                                               max_model_calls=2)))['run']['id']

    await runs.drive(identifier)

    blocked=await runs.get(identifier)
    assert blocked['state']=='blocked'
    assert blocked['rounds'][-1]['phase']=='invalid_response'
    assert blocked['error']['code']=='formal_script_specialists_missing'
    assert blocked['rounds'][-1]['usage']['input_tokens']==50
    assert blocked['rounds'][-1]['response_received'] is True
    with runs.read() as conn:
        attempt=conn.execute('SELECT state FROM agent_run_model_attempts WHERE run_id=?',
                             (identifier,)).fetchone()
        budget=conn.execute('SELECT used_model_calls FROM agent_run_model_budgets WHERE root_delegation_id=?',
                             (task['delegation_id'],)).fetchone()
    assert attempt['state']=='response_received' and budget['used_model_calls']==1
    assert len(calls)==1

    with pytest.raises(OperationError) as failure:
        await runs.control(identifier,'resume',{'request_id':'resume-after-host-validation',
                                               'expected_revision':blocked['revision']})
    assert failure.value.code=='formal_script_specialists_missing'
    await runs.drive(identifier)
    final=await runs.get(identifier)
    assert final['state']=='blocked'
    assert [step['phase'] for step in final['rounds']]==['invalid_response']
    assert len(calls)==1


async def test_saved_node_action_failure_after_decision_checkpoint_is_resumable_with_same_request(
        ctx,ready,service,runs,monkeypatch):
    from novelvideo.creation.agent_tools import AgentTools
    from .test_agent_profiles import configuration,save
    from .test_delegations import create,selection
    from .test_ontology import command

    graph_path=canvas_path(ctx.state_dir,ready[0])
    graph=json.loads(graph_path.read_text())
    node=next(item for item in graph['nodes'] if item['id']=='a')
    node.update(type='imageGenNode',data={'operation':'generate','prompt':'a rainy station'})
    graph_path.write_text(json.dumps(graph,ensure_ascii=False))

    profiles=ready[2]
    ref=await save(profiles,configuration(capabilities=[
        'context.read','proposal.create','node.patch','node.run']),request_id='node-failure-agent')
    adoption=await profiles.apply(command('adopt',{
        'ref':ref,'proof':profiles.library.version('agent',ref)['proof']},'node-failure-adopt'))
    node_task=await create(service,selection(ready,
        agent={'kind':'adoption','adoption_id':adoption['adoption_id'],'revision':1},
        scope={'kind':'nodes','node_ids':['a']},
        allowed_operations=['context.read','proposal.create','node.patch','node.run'],
        execution_authorization={
            'allowed_node_ids':['a'],
            'allowed_node_operations':[{'node_id':'a','operation':'generate'}],
            'max_node_runs':1,
        }), 'node-failure-task')

    model_calls=[]
    async def model(*args,**identity):
        model_calls.append(identity['attempt_id'])
        return decision({'operation':'node.run','payload':{
            'node_id':'a','operation':'generate','expected_revision':graph['revision']}})
    request_ids=[]
    async def fail_node_adapter(self,request,*,accepted_transition=None):
        request_ids.append(request.request_id)
        raise RuntimeError('simulated node adapter failure')

    monkeypatch.setattr(agent_model,'run_decision',model)
    monkeypatch.setattr(AgentTools,'_execute_node',fail_node_adapter)
    identifier=(await runs.start(start_request(node_task,max_model_calls=2)))['run']['id']

    await runs.drive(identifier)
    blocked=await runs.get(identifier)

    assert blocked['state']=='blocked'
    assert blocked['error']['code']=='node_execution_failed'
    assert blocked['rounds'][-1]['phase']=='decision_saved'
    assert blocked['rounds'][-1]['response_received'] is True
    assert blocked['rounds'][-1]['actions_admitted']==[0]
    assert blocked['rounds'][-1]['actions_done']==[]
    assert len(model_calls)==1 and len(request_ids)==1

    resumed=await runs.control(identifier,'resume',{
        'request_id':'resume-node-adapter-failure','expected_revision':blocked['revision']})
    assert resumed['run']['state']=='queued'
    await runs.drive(identifier)
    after_retry=await runs.get(identifier)

    assert after_retry['state']=='blocked'
    assert after_retry['error']['code']=='node_execution_failed'
    assert request_ids==[f"run:{identifier}:0:action:0"]*2
    assert len(model_calls)==1


@pytest.mark.parametrize('invalid_field', ['input_references','acceptance_indices'])
async def test_asset_design_indexes_outside_empty_frozen_inputs_are_rejected_before_decision_saved(
        ctx,ready,service,runs,task,monkeypatch,invalid_field):
    from novelvideo.creation.asset_adapter import registration_io
    from .test_delegations import create as create_delegation, selection

    delegation = await create_delegation(service, selection(
        ready,
        scope={'kind':'group','group_id':'group'},
        allowed_operations=['media.propose'],
        input_refs=[],
        acceptance=['标明版本 v2。','保持人物年龄与服装设定。','不修改其他组，不生成图片。'],
    ), 'asset-design-empty-inputs')
    payload = {
        'target_node_ids':['group'],
        'input_indices':[0,1,2] if invalid_field=='input_references' else [],
        'acceptance_indices':[0,1,2] if invalid_field=='input_references' else [3],
        'kind':'character',
        'name':'陈奶奶角色资产 v2',
        'description':'固定陈奶奶的年龄、发型与服装。',
        'prompt':'单人写实角色参考图，呈现脸部和上半身服装。',
        'reference_indices':[0] if invalid_field=='input_references' else [],
        'base_asset_input_index':0 if invalid_field=='input_references' else None,
        'draft_title':'陈奶奶角色设计 v2',
        'draft_text':'70岁中国女性，灰白短发，栗色针织开衫。',
    }
    prompts=[]

    async def model(_ctx,_route,prompt,**_identity):
        prompts.append(prompt)
        return decision({'operation':'asset.design.create','payload':payload})

    monkeypatch.setattr(agent_model,'run_decision',model)
    run=(await runs.start(start_request(delegation,request_id='asset-bad-frozen-index',
                                       max_model_calls=2)))['run']

    await runs.drive(run['id'])

    result=await runs.get(run['id'])
    raw=await registration_io(runs._read_value,run['id'],raw=True)
    assert len(prompts)==1 and '"inputs":[]' in prompts[0]
    assert result['state']=='blocked'
    assert result['rounds'][-1]['phase']=='invalid_response'
    assert 'decision' not in raw['rounds'][-1]
    assert result['rounds'][-1]['actions_admitted']==[]
    assert result['rounds'][-1]['actions_done']==[]
    assert result['error']['code']=='model_response_invalid'
    diagnostic=result['error']['diagnostic']
    assert diagnostic['stage']=='decision_payload'
    assert diagnostic['code']=='decision_contract_invalid'
    expected_locs=(
        [
            ['actions',0,'payload','input_indices',0],
            ['actions',0,'payload','input_indices',1],
            ['actions',0,'payload','input_indices',2],
            ['actions',0,'payload','reference_indices',0],
            ['actions',0,'payload','base_asset_input_index'],
        ] if invalid_field=='input_references' else [
            ['actions',0,'payload','acceptance_indices',0],
        ]
    )
    assert [issue['loc'] for issue in diagnostic['issues']]==expected_locs
    assert all(issue['type']=='index_out_of_range' for issue in diagnostic['issues'])
    with runs.read() as conn:
        attempt=conn.execute('SELECT state FROM agent_run_model_attempts WHERE run_id=?',
                             (run['id'],)).fetchone()
        budget=conn.execute('SELECT used_model_calls FROM agent_run_model_budgets WHERE root_delegation_id=?',
                             (delegation['delegation_id'],)).fetchone()
    assert attempt['state']=='response_received' and budget['used_model_calls']==1


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
    child={'task_key':'writer','adoption_id':adoption,'target_node_ids':['a'],'input_indices':[],
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
    assert len(final['outputs'])==1
    assert final['state']==('partial' if limit==1 else 'delivered'),final
    child=final['children'][0]
    assert child['state']==('partial' if limit==1 else 'delivered')
    assert child['child_run_id']
    child_contract=runs.delegations.get(child['delegation_id'])['contract']
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
    child={'task_key':'writer','adoption_id':adoption,'target_node_ids':['a'],'input_indices':[],
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
    assert calls==[1,1] and len(final['outputs'])==(1 if child_run else 2)
    assert final['unmet']==(['主创尚缺第二方案'] if child_run else [])
    assert final['state']==('partial' if child_run else 'delivered')


def _run_tools_lease_harness(tmp_path):
    class LeaseHarness:
        def __init__(self):
            self.ctx = SimpleNamespace(project_id='p', requester_user_id='alice',
                owner_username='alice', is_home_node=True, state_dir=tmp_path, output_dir=tmp_path)
            self.username = 'alice'
            self.canvas_id = 'canvas'
            self.conversation_id = 'conversation'
            self.state = 'applying'
            self.lease_expires = time.monotonic() + agent_runs.LEASE_SECONDS
            self.heartbeat_attempts = 0
            self.heartbeats = 0

        async def resolve_context(self, role):
            return self.ctx

        async def _authorize_node_action(self, *args, **kwargs):
            return None

        async def _authorize_node_submission(self, *args, **kwargs):
            return None

        def _authorize_node_projection_sync(self, *args, **kwargs):
            return None

        async def _send(self, action, payload):
            if action == 'admit_action':
                return {'run': {'rounds': [{'action_node_context': {'0': {'node_id': 'a'}}}]}}
            if action == 'heartbeat':
                self.heartbeat_attempts += 1
                if (self.state != 'applying' or self.lease_expires is None
                        or self.lease_expires <= time.monotonic()):
                    raise OperationError('run_fenced', 'stopped or expired')
                self.heartbeats += 1
                self.lease_expires = time.monotonic() + agent_runs.LEASE_SECONDS
                return {'run': {'state': self.state}}
            raise AssertionError(f'unexpected run action: {action}')

    return LeaseHarness()


def _accepted_node_transition():
    return {'agent_run_id': 'run-id', 'step_id': 'step-id',
            'action_id': 'action-id', 'action_index': 0}


def _node_run_action():
    return {'request_id': 'action-id', 'operation': 'node.run', 'payload': {
        'node_id': 'a', 'operation': 'generate', 'expected_revision': 1}}


async def test_admitted_slow_node_action_renews_agent_lease(tmp_path, monkeypatch):
    from novelvideo.creation.agent_tools import AgentTools

    monkeypatch.setattr(agent_runs, 'LEASE_SECONDS', .12)
    monkeypatch.setattr(agent_runs, 'HEARTBEAT_SECONDS', .01, raising=False)
    harness = _run_tools_lease_harness(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_provider(self, body, *, accepted_transition=None):
        entered.set()
        await release.wait()
        return {'receipt': 'durable'}

    monkeypatch.setattr(AgentTools, 'execute', slow_provider)
    tools = _RunTools(harness, 'run-id', 'owner-id', 'delegation-id',
                      step={'attempt_id': 'step-id'}, round_index=0)
    work = asyncio.create_task(tools.execute(_node_run_action(),
                                              accepted_transition=_accepted_node_transition()))
    await entered.wait()
    try:
        await asyncio.sleep(.28)
        assert harness.heartbeats >= 2
        assert harness.lease_expires > time.monotonic()
    finally:
        release.set()
    assert await work == {'receipt': 'durable'}
    heartbeats_after_receipt = harness.heartbeats
    await asyncio.sleep(.03)
    assert harness.heartbeats == heartbeats_after_receipt


async def test_stopping_slow_node_action_prevents_heartbeat_from_reviving_lease(tmp_path, monkeypatch):
    from novelvideo.creation.agent_tools import AgentTools

    monkeypatch.setattr(agent_runs, 'LEASE_SECONDS', .12)
    monkeypatch.setattr(agent_runs, 'HEARTBEAT_SECONDS', .01, raising=False)
    harness = _run_tools_lease_harness(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_provider(self, body, *, accepted_transition=None):
        entered.set()
        await release.wait()
        return {'receipt': 'provider-result-retained'}

    monkeypatch.setattr(AgentTools, 'execute', slow_provider)
    tools = _RunTools(harness, 'run-id', 'owner-id', 'delegation-id',
                      step={'attempt_id': 'step-id'}, round_index=0)
    work = asyncio.create_task(tools.execute(_node_run_action(),
                                              accepted_transition=_accepted_node_transition()))
    await entered.wait()
    harness.state = 'stopped'
    harness.lease_expires = None
    deadline = asyncio.get_running_loop().time() + .5
    while harness.heartbeat_attempts == 0 and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(.005)
    assert harness.heartbeat_attempts == 1
    assert harness.state == 'stopped' and harness.lease_expires is None
    release.set()
    assert await work == {'receipt': 'provider-result-retained'}
