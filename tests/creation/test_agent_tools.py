import asyncio
import json

import pytest

from novelvideo.creation.agent_tools import AgentTools
from novelvideo.creation.operations import OperationError
from novelvideo.creation.store import ObjectStore
from novelvideo.creation.ontology import Ontology
from novelvideo.freezone.canvas_store import canvas_path
from .test_delegations import ctx, service, ready, selection, create
from .test_ontology import command


@pytest.fixture
async def task(ready, service):
    agents=ready[2]
    entry=agents.builtin_catalog()[0]
    adopted=await agents.apply(command('adopt',{'source':'builtin','ref':entry['source']['ref'],
        'proof':entry['proof']},'builtin'))
    await service.apply(command('main.select',{'adoption_id':adopted['adoption_id'],
        'expected_adoption_revision':1,'expected_binding_revision':1},'main-builtin'))
    return await create(service,selection(ready,allowed_operations=[
        'context.read','proposal.create','ontology.definition.draft','media.propose']))


def broker(ctx,ready,task,**kwargs):
    async def resolve(role):
        return ctx
    return AgentTools(ctx,username='alice',canvas_id=ready[0],conversation_id=ready[1],
        delegation_id=task['delegation_id'],resolve_context=kwargs.pop('resolve_context',resolve),**kwargs)


def action(operation='proposal.create',request_id='write',**changes):
    payload={'target_node_ids':['a'],'input_indices':[],'acceptance_indices':[0]}
    if operation=='proposal.create':
        payload.update(title='开场方案',text='她在站台拾起一封未署名的信。')
    elif operation=='media.propose':
        payload.update(title='雨夜站台',intent='人物背影与空站台形成对比',modality='image')
    elif operation=='ontology.definition.draft':
        payload['definition']={'name':'clue','title':'线索','category':'object','fields':{'text':{'type':'string','required':True}}}
    payload.update(changes)
    return {'request_id':request_id,'operation':operation,'payload':payload}


async def test_real_handlers_create_only_candidates_and_atomic_replay(ctx,ready,task):
    tools=broker(ctx,ready,task)
    before=canvas_path(ctx.state_dir,ready[0]).read_bytes()
    result=await tools.execute(action())
    obj=ObjectStore(ctx.state_dir).read(result['output']['object_ref']['id'])
    assert obj['kind']=='story_fragment' and obj['revision']==1
    assert obj['payload']['text']=='她在站台拾起一封未署名的信。'
    assert obj['payload']['canvas_id']==ready[0]
    assert result['output']['state']=='candidate'
    assert await tools.execute(action())==result
    assert await tools.receipt('write')==result
    context=await tools.execute(action('context.read','read'))
    assert [n['node_id'] for n in context['output']['context']['nodes']]==['a']
    draft=await tools.execute(action('ontology.definition.draft','definition'))
    assert Ontology(ctx).definition('clue')['active_revision']==0
    assert draft['output']['definition_ref']=={'name':'clue','revision':1}
    media=await tools.execute(action('media.propose','media'))
    assert media['output']['execution_enabled'] is False
    assert media['output']['evaluation']['status']=='not_evaluated'
    assert canvas_path(ctx.state_dir,ready[0]).read_bytes()==before
    outputs=await tools.outputs()
    assert len(outputs)==4
    assert outputs[0]['object']['payload']['text']==obj['payload']['text']
    assert outputs[0]['object']['revision']==result['output']['object_ref']['revision']


@pytest.mark.parametrize('operation',['http','file.read','shell','definition.confirm','asset.bind','node.run'])
async def test_unknown_or_paid_operations_never_write(ctx,ready,task,operation):
    tools=broker(ctx,ready,task)
    with pytest.raises(OperationError):
        await tools.execute(action(operation))
    assert await tools.outputs()==[] and await tools.receipt('write') is None


async def test_targets_inputs_authority_and_request_collision(ctx,ready,task):
    tools=broker(ctx,ready,task)
    for changes in ({'target_node_ids':['other']},{'target_node_ids':[]},{'input_indices':[0]},
                    {'acceptance_indices':[1]},{'authority':'user'}):
        with pytest.raises(OperationError):
            await tools.execute(action(**changes))
    first=await tools.execute(action())
    with pytest.raises(OperationError) as error:
        await tools.execute(action(text='不同文本'))
    assert error.value.code=='idempotency_conflict'
    assert await tools.receipt('write')==first


async def test_layout_allowed_content_change_stale_and_replay_preserved(ctx,ready,task):
    tools=broker(ctx,ready,task)
    path=canvas_path(ctx.state_dir,ready[0]);graph=json.loads(path.read_text())
    graph['nodes'][1]['position']={'x':200,'y':300};graph['revision']+=1;path.write_text(json.dumps(graph))
    original=await tools.execute(action())
    graph['nodes'][1]['data']['text']='人工新开场';path.write_text(json.dumps(graph))
    with pytest.raises(OperationError) as error:
        await tools.execute(action(request_id='late'))
    assert error.value.code=='stale_input'
    assert await tools.execute(action())==original


async def test_acl_is_rechecked_after_waiting_for_project_lock(ctx,ready,task):
    from novelvideo.production_workflow.coordination import asset_project_lock
    allowed=True;entered=asyncio.Event();release=asyncio.Event()
    async def resolve(role):
        if not allowed:
            raise OperationError('scope_violation','权限已撤销')
        return ctx
    async def holder():
        async with asset_project_lock(ctx.state_dir):
            entered.set();await release.wait()
    holding=asyncio.create_task(holder());await entered.wait()
    tools=broker(ctx,ready,task,resolve_context=resolve)
    pending=asyncio.create_task(tools.execute(action()));await asyncio.sleep(.02)
    allowed=False;release.set();await holding
    with pytest.raises(OperationError):
        await pending
    allowed=True
    assert await tools.outputs()==[]


@pytest.mark.parametrize('phase',['before_commit','after_commit'])
async def test_commit_failure_recovery_uses_original_request(ctx,ready,task,monkeypatch,phase):
    from novelvideo.creation import ontology
    tools=broker(ctx,ready,task)
    def crash(at):
        if at==phase:
            raise RuntimeError('interrupted')
    monkeypatch.setattr(ontology,'_checkpoint',crash)
    with pytest.raises(RuntimeError):
        await tools.execute(action())
    assert len(await tools.outputs())==(1 if phase=='after_commit' else 0)
    monkeypatch.setattr(ontology,'_checkpoint',lambda _:None)
    result=await tools.execute(action())
    assert len(await tools.outputs())==1
    assert await tools.receipt('write')==result


async def test_revoke_blocks_new_actions_not_history(ctx,ready,task,service):
    tools=broker(ctx,ready,task);original=await tools.execute(action())
    await service.apply(command('delegation.revoke',{'delegation_id':task['delegation_id'],
        'expected_revision':1,'reason':'停止'},'revoke'))
    with pytest.raises(OperationError):
        await tools.execute(action(request_id='new'))
    assert await tools.execute(action())==original
    assert len(await tools.outputs())==1


async def test_parallel_same_request_creates_one_object(ctx,ready,task):
    results=await asyncio.gather(*(broker(ctx,ready,task).execute(action()) for _ in range(3)))
    assert results[0]==results[1]==results[2]
    assert len(await broker(ctx,ready,task).outputs())==1


@pytest.mark.parametrize('change',['new','revise','demote'])
async def test_current_hard_change_returns_structured_diff(ctx,ready,service,change):
    from novelvideo.creation.creative_constraints import CreativeConstraints
    from .test_creative_constraints import create as constraint, confirm
    constraints=CreativeConstraints(ctx,authority='user')
    hard=None
    if change!='new':
        hard=await confirm(constraints,await constraint(constraints))
    task=await create(service,selection(ready))
    if change=='new':
        await confirm(constraints,await constraint(constraints))
    elif change=='demote':
        await confirm(constraints,hard,'draft','demote')
    else:
        await constraints.apply(command('constraint.confirm',{'constraint_id':hard['constraint_id'],
            'expected_revision':hard['revision'],'level':'hard','basis':'明确修订','value':'cold'},'revise'))
    tools=broker(ctx,ready,task)
    with pytest.raises(OperationError) as error:
        await tools.execute(action())
    detail=error.value.as_dict()
    assert detail['code']=='hard_constraints_changed'
    assert detail['before']!=detail['after']
    assert await tools.outputs()==[]


async def test_session_clear_and_ancestor_revoke_are_fences(ctx,ready,service,task):
    from novelvideo.chat.session_store import for_scope
    child=await create(service,selection(ready,parent_id=task['delegation_id'],
        scope={'kind':'nodes','node_ids':['a']}),'child')
    tools=broker(ctx,ready,child)
    await service.apply(command('delegation.revoke',{'delegation_id':task['delegation_id'],
        'expected_revision':1,'reason':'停止父任务'},'stop-parent'))
    with pytest.raises(OperationError) as error:
        await tools.execute(action())
    assert error.value.code=='delegation_inactive'
    other=await create(service,selection(ready),'other-task')
    store=for_scope('alice','project','p',conversation_id=ready[1])
    state,_=store.peek();store.clear(expected_generation=state.generation,request_id='clear')
    with pytest.raises(OperationError) as error:
        await broker(ctx,ready,other).execute(action())
    assert error.value.code=='stale_input'


async def test_cross_canvas_inputs_are_read_only_and_output_change_is_stale(ctx,ready,service):
    from novelvideo.creation.canvas_workspaces import CanvasWorkspace,CanvasCreate
    from novelvideo.creation.projection import ProjectionService
    store=ObjectStore(ctx.state_dir)
    created=CanvasWorkspace(store,project_id='p').create('alice',CanvasCreate(request_id='second',kind='free',title='另一个画布'))
    ProjectionService(store).apply(created.operation_id)
    other=created.object_refs[0]['canvas_id'];path=canvas_path(ctx.state_dir,other)
    graph=json.loads(path.read_text());graph['nodes']=[{'id':'source','type':'imageGenNode',
        'data':{'prompt':'输入','imageUrl':'/static/p/old.png'}}];path.write_text(json.dumps(graph))
    task=await create(service,selection(ready,input_refs=[{'kind':'canvas_node','canvas_id':other,'node_id':'source'}]))
    tools=broker(ctx,ready,task)
    with pytest.raises(OperationError):
        await tools.execute(action(target_node_ids=['source'],input_indices=[0]))
    result=await tools.execute(action('context.read','read',input_indices=[0]))
    evidence=result['output']['context']['inputs'][0]['snapshot']['media_evidence']
    assert evidence['byte_stability']=='not_archived' and evidence['status']=='not_evaluated'
    graph['nodes'][0]['data']['imageUrl']='/static/p/new.png';path.write_text(json.dumps(graph))
    with pytest.raises(OperationError) as error:
        await tools.execute(action(request_id='new',input_indices=[0]))
    assert error.value.code=='stale_input'


async def test_definition_cannot_overwrite_or_confirm_and_no_generic_payload(ctx,ready,task):
    tools=broker(ctx,ready,task)
    first=await tools.execute(action('ontology.definition.draft','draft'))
    with pytest.raises(OperationError):
        await tools.execute(action('ontology.definition.draft','overwrite'))
    invalid=action('ontology.definition.draft','reserved')
    invalid['payload']['definition']['name']='character'
    with pytest.raises(OperationError):
        await tools.execute(invalid)
    with pytest.raises(OperationError):
        await tools.execute(action('media.propose','path',path='/tmp/arbitrary'))
    assert len(await tools.outputs())==1
    assert first['output']['state']=='draft'
    assert Ontology(ctx).definition('clue')['source']['kind']=='agent'


async def test_receipts_bound_to_exact_delegation_and_owner(ctx,ready,task,service):
    tools=broker(ctx,ready,task)
    result=await tools.execute(action())
    other=await create(service,selection(ready),'another-task')
    other_tools=broker(ctx,ready,other)
    assert await other_tools.receipt('write') is None
    assert await other_tools.outputs()==[]
    with pytest.raises(OperationError) as error:
        await other_tools.execute(action())
    assert error.value.code=='idempotency_conflict'
    tools.username='other-login'
    tools.delegations.username='other-login'
    with pytest.raises(OperationError):
        await tools.receipt('write')
    assert await broker(ctx,ready,task).receipt('write')==result


async def test_completed_receipt_survives_missing_input_canvas(ctx,ready,task):
    tools=broker(ctx,ready,task)
    result=await tools.execute(action())
    canvas_path(ctx.state_dir,ready[0]).unlink()
    assert await tools.execute(action())==result
    assert await tools.receipt('write')==result
    assert len(await tools.outputs())==1
    with pytest.raises(OperationError):
        await tools.execute(action(request_id='new'))


async def test_cancelled_write_keeps_lock_until_worker_and_receipt_complete(ctx,ready,task,monkeypatch):
    import threading
    from novelvideo.creation import ontology
    from novelvideo.production_workflow.coordination import asset_project_lock
    reached=threading.Event();release=threading.Event();acquired=asyncio.Event()
    def pause(phase):
        if phase=='before_commit':
            reached.set()
            assert release.wait(5)
    monkeypatch.setattr(ontology,'_checkpoint',pause)
    tools=broker(ctx,ready,task)
    running=asyncio.create_task(tools.execute(action()))
    assert await asyncio.to_thread(reached.wait,5)
    running.cancel()
    async def waiting():
        async with asset_project_lock(ctx.state_dir):
            acquired.set()
    waiter=asyncio.create_task(waiting())
    await asyncio.sleep(.03)
    assert not acquired.is_set()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await running
    await waiter
    receipt=await tools.receipt('write')
    assert receipt and len(await tools.outputs())==1
    assert await tools.execute(action())==receipt
