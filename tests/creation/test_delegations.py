import json
from types import SimpleNamespace

import pytest

from novelvideo.creation.delegations import Delegations
from novelvideo.creation.agent_profiles import AgentProfiles
from novelvideo.creation.personal_library import PersonalLibrary
from novelvideo.creation.operations import OperationError
from novelvideo.creation.store import ObjectStore
from novelvideo.creation.canvas_workspaces import CanvasWorkspace, CanvasCreate
from novelvideo.creation.projection import ProjectionService
from novelvideo.freezone.canvas_store import canvas_path
from novelvideo.chat.conversations import ConversationStore
from .test_agent_profiles import save, configuration
from .test_ontology import command


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv('NOVELVIDEO_STATE_DIR', str(tmp_path / 'sessions'))
    return SimpleNamespace(project_id='p', requester_user_id='alice', owner_username='alice',
                           is_home_node=True, state_dir=tmp_path/'state', output_dir=tmp_path/'media')


@pytest.fixture
def service(ctx):
    return Delegations(ctx, username='alice', authority='user')


@pytest.fixture
async def ready(ctx, service, tmp_path):
    ctx.state_dir.mkdir()
    (ctx.state_dir/'project_config.json').write_text(json.dumps({'creation_mode':'agent_canvas_v1'}))
    store = ObjectStore(ctx.state_dir)
    created = CanvasWorkspace(store, project_id='p').create('alice', CanvasCreate(request_id='canvas', kind='free', title='画布'))
    ProjectionService(store).apply(created.operation_id)
    cid = created.object_refs[0]['canvas_id']
    graph = json.loads(canvas_path(ctx.state_dir, cid).read_text())
    graph['nodes'] = [
        {'id':'group','type':'groupNode','data':{'label':'段落'}},
        {'id':'a','type':'textAnnotationNode','parentId':'group','data':{'text':'开场 A'}},
        {'id':'b','type':'textAnnotationNode','parentId':'group','data':{'text':'开场 B'}},
        {'id':'other','type':'textAnnotationNode','data':{'text':'对照'}},
    ]
    canvas_path(ctx.state_dir, cid).write_text(json.dumps(graph))
    conversation = ConversationStore('alice','project','p').create('讨论', canvas_id=cid, request_id='conversation')
    agents = AgentProfiles(ctx, PersonalLibrary(tmp_path/'personal','alice'))
    ref = await save(agents, configuration())
    adopted = await agents.apply(command('adopt', {'ref':ref,'proof':agents.library.version('agent',ref)['proof']}, 'adopt'))
    await service.apply(command('main.select', {'adoption_id':adopted['adoption_id'], 'expected_adoption_revision':1,'expected_binding_revision':0}, 'select'))
    return cid, conversation['id'], agents, adopted


def selection(ready, **changes):
    cid, conversation, *_ = ready
    return {'canvas_id':cid,'conversation_id':conversation,'scope':{'kind':'nodes','node_ids':['a','b']},
            'agent':{'kind':'main'},'input_refs':[], 'goal':'提出两套悬疑开场',
            'allowed_operations':['context.read','proposal.create'], 'acceptance':['两套文本方案'], **changes}


async def create(service, body, request_id='task'):
    preview = service.preview(body)
    return await service.apply(command('delegation.create', {**body,'preview_proof':preview['proof']}, request_id))


def edit_graph(ctx, cid, edit):
    path = canvas_path(ctx.state_dir, cid)
    graph = json.loads(path.read_text())
    edit(graph)
    graph['revision'] += 1
    path.write_text(json.dumps(graph))


def test_empty_reads_do_not_initialize(service, ctx):
    assert service.main_agent()['adoption_id'] is None
    assert service.receipt('unknown') is None
    assert not ctx.state_dir.exists()


async def test_new_tasks_follow_project_override_old_tasks_and_receipts_are_fixed(service, ready):
    body = selection(ready)
    first = await create(service, body)
    assert first['status'] == 'prepared'
    assert first['contract']['agent']['revision'] == 1
    agents, adopted = ready[2:]
    changes = {'methods':'新的项目方法'}
    preview = agents.override_preview(adopted['adoption_id'], changes)
    await agents.apply(command('override', {'adoption_id':adopted['adoption_id'], 'expected_revision':1,
        'changes':changes,'proof':preview['proof']}, 'override'))
    second = await create(service, body, 'task2')
    assert second['contract']['agent']['revision'] == 2
    assert second['contract']['agent']['effective']['methods'] == '新的项目方法'
    assert service.get(first['delegation_id'])['contract']['agent']['effective']['methods'] == '先核对事实'
    assert service.receipt('task') == first


async def test_layout_change_allowed_content_or_membership_change_stale(service, ready, ctx):
    body = selection(ready)
    preview = service.preview(body)
    edit_graph(ctx, ready[0], lambda g:g['nodes'][1].update(position={'x':500,'y':10}))
    result = await service.apply(command('delegation.create', {**body,'preview_proof':preview['proof']}, 'layout'))
    assert result['status'] == 'prepared'
    preview = service.preview(body)
    edit_graph(ctx, ready[0], lambda g:g['nodes'][1]['data'].update(text='人工修改'))
    with pytest.raises(OperationError, match='变化'):
        await service.apply(command('delegation.create', {**body,'preview_proof':preview['proof']}, 'stale'))
    assert service.receipt('stale') is None


@pytest.mark.parametrize('change', [
    {'scope':{'kind':'nodes','node_ids':[]}},
    {'scope':{'kind':'nodes','node_ids':['missing']}},
    {'allowed_operations':['shell.execute']},
    {'authority':'user'},
    {'conversation_id':'default'},
])
async def test_invalid_contract_cannot_grant_permissions(service, ready, change):
    with pytest.raises((OperationError, ValueError)):
        service.preview(selection(ready, **change))


async def test_child_only_narrows_and_revoke_blocks_descendants(service, ready):
    parent = await create(service, selection(ready, scope={'kind':'group','group_id':'group'}))
    body = selection(ready, scope={'kind':'nodes','node_ids':['a']}, parent_id=parent['delegation_id'],
                     allowed_operations=['context.read'])
    child = await create(service, body, 'child')
    assert child['contract']['parent_id'] == parent['delegation_id']
    with pytest.raises(OperationError):
        service.preview({**body,'scope':{'kind':'nodes','node_ids':['other']}})
    await service.apply(command('delegation.revoke', {'delegation_id':parent['delegation_id'],
        'expected_revision':1,'reason':'停止后续操作'}, 'stop'))
    assert service.get(child['delegation_id'])['effective_state'] == 'revoked'
    with pytest.raises(OperationError):
        service.preview(body)


async def test_execution_authorization_is_frozen_node_scoped_and_child_only_shrinks(service, ready):
    agents = ready[2]
    ref = await save(agents, configuration(capabilities=['context.read', 'proposal.create', 'node.patch', 'node.run']), request_id='auth-agent-save')
    adoption = await agents.apply(command('adopt', {'ref': ref, 'proof': agents.library.version('agent', ref)['proof']}, 'auth-agent'))
    grant = {
        'allowed_node_ids': ['a', 'b'],
        'allowed_node_operations': [
            {'node_id': 'a', 'operation': 'patch'},
            {'node_id': 'a', 'operation': 'validate'},
            {'node_id': 'b', 'operation': 'validate'},
        ],
        'max_node_runs': 2,
    }
    allowed = ['context.read', 'proposal.create', 'node.patch', 'node.run']
    parent = await create(service, selection(ready, agent={'kind': 'adoption',
        'adoption_id': adoption['adoption_id'], 'revision': 1}, allowed_operations=allowed,
        input_refs=[{'kind': 'canvas_node', 'canvas_id': ready[0], 'node_id': 'other'}],
        execution_authorization=grant))
    contract = parent['contract']
    assert contract['execution_authorization'] == grant
    assert contract['read_node_ids'] == ['a', 'b', 'other']
    assert contract['authorization_id']
    assert contract['root_delegation_id'] == parent['delegation_id']
    assert contract['root_max_node_runs'] == 2
    assert contract['node_run_budget_chain'] == [
        {'delegation_id': parent['delegation_id'], 'max_node_runs': 2}]

    # A child that receives its own explicit, narrower grant shares the root
    # authorization identity and cannot widen nodes, operations, or run count.
    from novelvideo.creation.delegations import Delegations
    child_service = Delegations(service.ctx, username='alice', authority='agent', parent_id=parent['delegation_id'])
    child_body = selection(ready, agent={'kind': 'adoption', 'adoption_id': adoption['adoption_id'], 'revision': 1},
        scope={'kind': 'nodes', 'node_ids': ['a']},
        parent_id=parent['delegation_id'], allowed_operations=['context.read', 'node.run'],
        execution_authorization={
            'allowed_node_ids': ['a'],
            'allowed_node_operations': [{'node_id': 'a', 'operation': 'validate'}],
            'max_node_runs': 1,
        })
    child = await create(child_service, child_body, 'auth-child')
    assert child['contract']['execution_authorization'] == child_body['execution_authorization']
    assert child['contract']['read_node_ids'] == ['a']
    assert child['contract']['node_run_budget_chain'] == [
        {'delegation_id': parent['delegation_id'], 'max_node_runs': 2},
        {'delegation_id': child['delegation_id'], 'max_node_runs': 1}]
    assert child['contract']['authorization_id'] == contract['authorization_id']
    assert child['contract']['root_delegation_id'] == parent['delegation_id']

    grandchild_service = Delegations(service.ctx, username='alice', authority='agent',
                                     parent_id=child['delegation_id'])
    grandchild_body = selection(ready, agent={'kind': 'adoption', 'adoption_id': adoption['adoption_id'], 'revision': 1},
        scope={'kind': 'nodes', 'node_ids': ['a']}, parent_id=child['delegation_id'],
        allowed_operations=['context.read', 'node.run'], execution_authorization={
            'allowed_node_ids': ['a'],
            'allowed_node_operations': [{'node_id': 'a', 'operation': 'validate'}],
            'max_node_runs': 1,
        })
    grandchild = await create(grandchild_service, grandchild_body, 'auth-grandchild')
    assert grandchild['contract']['node_run_budget_chain'] == [
        {'delegation_id': parent['delegation_id'], 'max_node_runs': 2},
        {'delegation_id': child['delegation_id'], 'max_node_runs': 1},
        {'delegation_id': grandchild['delegation_id'], 'max_node_runs': 1}]

    for child_grant in (
        {**child_body['execution_authorization'], 'allowed_node_ids': ['b']},
        {**child_body['execution_authorization'], 'allowed_node_operations': [
            {'node_id': 'a', 'operation': 'patch'}]},
        {**child_body['execution_authorization'], 'max_node_runs': 3},
    ):
        with pytest.raises(OperationError):
            child_service.preview({**child_body, 'execution_authorization': child_grant})


def test_execution_authorization_accepts_run_only_for_registered_skill_operation(service):
    expanded = {'node_ids': ['skill'], 'nodes': [{'node_id': 'skill', 'node_type': 'skillNode'}]}
    authorization = service._execution_authorization({
        'allowed_node_ids': ['skill'],
        'allowed_node_operations': [{'node_id': 'skill', 'operation': 'run'}],
        'max_node_runs': 1,
    }, expanded, ['node.run'])
    assert authorization['allowed_node_operations'] == [{'node_id': 'skill', 'operation': 'run'}]

    wrong_type = {'node_ids': ['text'], 'nodes': [{'node_id': 'text', 'node_type': 'textAnnotationNode'}]}
    with pytest.raises(OperationError):
        service._execution_authorization({
            'allowed_node_ids': ['text'],
            'allowed_node_operations': [{'node_id': 'text', 'operation': 'run'}],
            'max_node_runs': 1,
        }, wrong_type, ['node.run'])


async def test_child_preview_freezes_only_parent_accepted_patch_transition(service, ready, ctx):
    from novelvideo.creation.agent_tools import AgentTools

    cid = ready[0]
    edit_graph(ctx, cid, lambda graph: graph['nodes'][1].update(type='storyboardGenNode', data={
        'prompt': '原始分镜', 'frames': [{'static_panel': {'state': '待定', 'framing': '中景',
            'setting': '站台', 'lighting': '平光', 'visible_text': [], 'subjects': []}}]}))
    agents = ready[2]
    ref = await save(agents, configuration(capabilities=['context.read', 'node.patch']), request_id='accepted-patch-agent')
    adoption = await agents.apply(command('adopt', {
        'ref': ref, 'proof': agents.library.version('agent', ref)['proof']}, 'accepted-patch-adopt'))
    parent_body = selection(ready, agent={'kind': 'adoption', 'adoption_id': adoption['adoption_id'], 'revision': 1},
        scope={'kind': 'nodes', 'node_ids': ['a']}, allowed_operations=['context.read', 'node.patch'],
        execution_authorization={'allowed_node_ids': ['a'],
            'allowed_node_operations': [{'node_id': 'a', 'operation': 'patch'}], 'max_node_runs': 0})
    parent = await create(service, parent_body, 'accepted-patch-parent')

    async def resolve(role):
        return ctx

    tools = AgentTools(ctx, username='alice', canvas_id=cid, conversation_id=ready[1],
        delegation_id=parent['delegation_id'], resolve_context=resolve)
    revision = json.loads(canvas_path(ctx.state_dir, cid).read_text())['revision']
    patch = await tools.execute({'request_id': 'accepted-lighting-patch', 'operation': 'node.patch',
        'payload': {'node_id': 'a', 'expected_revision': revision,
            'changes': [{'path': ['frames', 0, 'static_panel', 'lighting'], 'value': '冷色侧光'}]}})
    output = patch['output']
    assert output['state'] == 'patched'
    assert output['changes'] == [{'path': ['frames', 0, 'static_panel', 'lighting'],
                                  'before': '平光', 'after': '冷色侧光'}]

    child_service = Delegations(service.ctx, username='alice', authority='agent', parent_id=parent['delegation_id'])
    child_body = selection(ready, parent_id=parent['delegation_id'],
        agent={'kind': 'adoption', 'adoption_id': adoption['adoption_id'], 'revision': 1},
        scope={'kind': 'nodes', 'node_ids': ['a']}, allowed_operations=['context.read'])
    child_preview = child_service.preview(child_body)
    frozen = child_preview['contract']['expanded_scope']['nodes'][0]
    assert frozen['input_fingerprint'] == output['accepted_after']['input_fingerprint']
    assert child_preview['contract']['parent_transition_evidence'] == [{
        'delegation_id': parent['delegation_id'], 'output_id': output['id'],
        'operation_id': output['operation_id'], 'operation': 'node.patch', 'node_run_id': None}]

    edit_graph(ctx, cid, lambda graph: graph['nodes'][1]['data']['frames'][0]['static_panel']
               .update(lighting='人工外部修改'))
    with pytest.raises(OperationError) as stale:
        child_service.preview(child_body)
    assert stale.value.code == 'stale_input'


async def test_missing_execution_authorization_is_preparation_only_and_cannot_be_added_by_child(service, ready):
    agents = ready[2]
    ref = await save(agents, configuration(capabilities=['context.read', 'proposal.create', 'node.run']), request_id='prep-only-agent-save')
    adoption = await agents.apply(command('adopt', {'ref': ref, 'proof': agents.library.version('agent', ref)['proof']}, 'prep-only-agent'))
    parent = await create(service, selection(ready, agent={'kind': 'adoption',
        'adoption_id': adoption['adoption_id'], 'revision': 1}, allowed_operations=['context.read', 'node.run']))
    assert parent['contract']['execution_authorization'] is None
    assert parent['contract']['authorization_id'] is None
    from novelvideo.creation.delegations import Delegations
    child_service = Delegations(service.ctx, username='alice', authority='agent', parent_id=parent['delegation_id'])
    body = selection(ready, scope={'kind': 'nodes', 'node_ids': ['a']}, parent_id=parent['delegation_id'],
        allowed_operations=['context.read', 'node.run'], execution_authorization={
            'allowed_node_ids': ['a'],
            'allowed_node_operations': [{'node_id': 'a', 'operation': 'validate'}],
            'max_node_runs': 1,
        })
    with pytest.raises(OperationError):
        child_service.preview(body)


@pytest.mark.parametrize('grant', [
    {'allowed_node_ids': ['other'], 'allowed_node_operations': [{'node_id': 'other', 'operation': 'validate'}], 'max_node_runs': 1},
    {'allowed_node_ids': ['a'], 'allowed_node_operations': [{'node_id': 'b', 'operation': 'patch'}], 'max_node_runs': 1},
    {'allowed_node_ids': ['a'], 'allowed_node_operations': [{'node_id': 'a', 'operation': 'shell'}], 'max_node_runs': 1},
    {'allowed_node_ids': ['a'], 'allowed_node_operations': [{'node_id': 'a', 'operation': 'validate'}], 'max_node_runs': 33},
    {'allowed_node_ids': ['a'], 'allowed_node_operations': [{'node_id': 'a', 'operation': 'run'}], 'max_node_runs': 1},
])
async def test_execution_authorization_rejects_scope_operation_and_budget_widening(service, ready, grant):
    agents = ready[2]
    ref = await save(agents, configuration(capabilities=['context.read', 'proposal.create', 'node.patch', 'node.run']), request_id='invalid-auth-agent-save')
    adoption = await agents.apply(command('adopt', {'ref': ref, 'proof': agents.library.version('agent', ref)['proof']}, 'invalid-auth-agent'))
    with pytest.raises(OperationError):
        service.preview(selection(ready, agent={'kind': 'adoption',
            'adoption_id': adoption['adoption_id'], 'revision': 1},
            allowed_operations=['context.read', 'proposal.create', 'node.patch', 'node.run'],
            execution_authorization=grant))


async def test_wrong_user_cannot_read_or_replay_private_delegation(service, ready, ctx):
    result = await create(service, selection(ready))
    other = Delegations(SimpleNamespace(**{**vars(ctx),'requester_user_id':'bob'}), username='bob', authority='user')
    with pytest.raises(OperationError):
        other.get(result['delegation_id'])
    assert other.receipt('task') is None


async def test_replay_checks_username_even_with_same_actor(service, ready, ctx):
    body = selection(ready)
    request = command('delegation.create', {**body, 'preview_proof':service.preview(body)['proof']}, 'task')
    await service.apply(request)
    other = Delegations(ctx, username='different-login', authority='user')
    with pytest.raises(OperationError):
        await other.apply(request)


async def test_changed_generated_output_is_evidence_not_file_authorization(service, ready, ctx):
    def set_output(graph):
        graph['nodes'][1].update(type='imageGenNode', data={'prompt':'同一个提示词','imageUrl':'/static/p/one.png'})
    edit_graph(ctx, ready[0], set_output)
    body = selection(ready)
    preview = service.preview(body)
    snapshot = preview['contract']['expanded_scope']['nodes'][0]
    assert snapshot['media_evidence']['status'] == 'not_evaluated'
    assert snapshot['media_evidence']['byte_stability'] == 'not_archived'
    edit_graph(ctx, ready[0], lambda g:g['nodes'][1]['data'].update(imageUrl='/static/p/two.png'))
    with pytest.raises(OperationError):
        await service.apply(command('delegation.create', {**body,'preview_proof':preview['proof']}, 'output-changed'))


async def test_child_cannot_widen_mode_or_ignore_ancestor_membership(service, ready, ctx):
    parent = await create(service, selection(ready))
    child = selection(ready, parent_id=parent['delegation_id'], scope={'kind':'nodes','node_ids':['a']})
    edit_graph(ctx, ready[0], lambda g:g['nodes'].append({'id':'outer','type':'groupNode','data':{}}))
    edit_graph(ctx, ready[0], lambda g:g['nodes'][0].update(parentId='outer'))
    with pytest.raises(OperationError):
        service.preview(child)


@pytest.mark.parametrize('phase', ['before_commit', 'after_commit'])
async def test_atomic_crash_and_original_request_recovery(service, ready, monkeypatch, phase):
    from novelvideo.creation import ontology
    body = selection(ready)
    request = command('delegation.create', {**body,'preview_proof':service.preview(body)['proof']}, 'crash')
    def crash(at):
        if at == phase:
            raise RuntimeError('interrupted')
    monkeypatch.setattr(ontology, '_checkpoint', crash)
    with pytest.raises(RuntimeError):
        await service.apply(request)
    assert len(service.catalog(ready[0], ready[1])) == (phase == 'after_commit')
    monkeypatch.setattr(ontology, '_checkpoint', lambda _:None)
    result = await service.apply(request)
    assert service.receipt('crash') == result
    assert len(service.catalog(ready[0], ready[1])) == 1
    with pytest.raises(OperationError) as error:
        await service.apply({**request,'payload':{**request['payload'],'goal':'another'}})
    assert error.value.code == 'idempotency_conflict'


async def test_concurrent_duplicate_and_main_cas(service, ready):
    import asyncio
    body = selection(ready)
    request = command('delegation.create', {**body,'preview_proof':service.preview(body)['proof']}, 'parallel')
    first, second = await asyncio.gather(service.apply(request), service.apply(request))
    assert first == second and len(service.catalog(ready[0], ready[1])) == 1
    results = await asyncio.gather(*[service.apply(command('main.clear', {'expected_binding_revision':1}, f'clear-{i}'))
                                     for i in range(2)], return_exceptions=True)
    assert sum(isinstance(r, OperationError) for r in results) == 1
    assert service.main_agent()['binding_revision'] == 2
    # Receipt remains the original prepared result after selection was cleared.
    assert await service.apply(request) == first


async def test_agent_cannot_create_root_confirm_or_self_assert_parent(service, ready, ctx):
    agent = Delegations(ctx, username='alice')
    with pytest.raises(OperationError):
        agent.preview(selection(ready))
    with pytest.raises(OperationError):
        await agent.apply(command('main.clear', {'expected_binding_revision':1}, 'agent-clear'))
    parent = await create(service, selection(ready))
    body = selection(ready, parent_id=parent['delegation_id'], scope={'kind':'nodes','node_ids':['a']})
    with pytest.raises(OperationError):
        agent.preview(body)
    trusted = Delegations(ctx, username='alice', parent_id=parent['delegation_id'])
    child = await create(trusted, body, 'trusted-child')
    assert child['contract']['authority']['grant'] == 'parent_delegation'
    assert child['contract']['authority']['text_is_permission'] is False


async def test_constraints_and_per_node_direction_are_fixed_without_promoting_drafts(service, ready, ctx):
    from novelvideo.creation.creative_constraints import CreativeConstraints
    from novelvideo.creation.library_references import DirectionReferences
    from .test_creative_constraints import create as agreement, confirm
    from .test_direction_references import request as direction_request
    constraints = CreativeConstraints(ctx, authority='user')
    draft = await agreement(constraints, key='identity', value='同一个人物', request_id='fact-draft')
    hard = await confirm(constraints, draft, request_id='fact-confirm')
    agent_draft = await agreement(CreativeConstraints(ctx), key='suggestion', value='猜测', request_id='agent-draft')
    directions = DirectionReferences(ctx)
    styles = directions.catalog('style')
    for node_id, entry in zip(['a','b'], styles[:2]):
        await directions.apply(direction_request(directions, {'level':'node','canvas_id':ready[0],'node_id':node_id},
                                                  entry, request_id='style-'+node_id))
    task = await create(service, selection(ready))
    contexts = task['contract']['context']
    assert [c['direction']['effective']['style']['title'] for c in contexts] == [s['title'] for s in styles[:2]]
    for frozen in contexts:
        assert frozen['agreements']['effective']['identity']['revision'] == hard['revision']
        assert frozen['agreements']['drafts'][0]['constraint_id'] == agent_draft['constraint_id']
        assert frozen['agreements']['drafts'][0]['level'] == 'draft'
    await confirm(constraints, hard, 'draft', 'demote')
    assert service.get(task['delegation_id'])['contract'] == task['contract']


async def test_cross_canvas_read_does_not_expand_write_domain(service, ready, ctx):
    store = ObjectStore(ctx.state_dir)
    created = CanvasWorkspace(store, project_id='p').create('alice', CanvasCreate(request_id='other-canvas', kind='free', title='别处'))
    ProjectionService(store).apply(created.operation_id)
    cid = created.object_refs[0]['canvas_id']
    edit_graph(ctx, cid, lambda g:g['nodes'].append({'id':'remote','type':'textAnnotationNode','data':{'text':'明确引用'}}))
    inputs = [{'kind':'canvas_node','canvas_id':cid,'node_id':'remote'}]
    body = selection(ready, input_refs=inputs)
    task = await create(service, body)
    assert task['contract']['inputs'][0]['snapshot']['content']['text'] == '明确引用'
    assert task['contract']['expanded_scope']['node_ids'] == ['a','b']
    with pytest.raises(OperationError):
        service.preview({**body,'parent_id':task['delegation_id'],'canvas_id':cid})
    with pytest.raises(OperationError):
        service.preview({**body,'parent_id':task['delegation_id'],'input_refs':inputs+[{'kind':'canvas_node','canvas_id':ready[0],'node_id':'other'}]})


async def test_custom_fixed_object_schema_history_is_not_user_confirmed_fact(service, ready, ctx):
    from novelvideo.creation.ontology import Ontology
    from .test_ontology import activate
    domain = Ontology(ctx, authority='user')
    await activate(domain)
    obj = await domain.apply(command('object.create', {'definition':'clue','schema_revision':1,
        'scope':{'level':'project'},'data':{'text':'线索内容'}}, 'object'))
    task = await create(service, selection(ready, input_refs=[{'kind':'object','object_id':obj['object_id'],'revision':1}]))
    frozen = task['contract']['inputs'][0]
    assert frozen['snapshot']['payload']['data']['text'] == '线索内容'
    assert frozen['definition']['revision'] == 1
    assert task['contract']['context'][-1]['agreements']['effective'] == {}


async def test_missing_session_cannot_be_recreated_by_preview(service, ready, ctx):
    from novelvideo.chat.session_store import for_scope
    store = for_scope('alice','project','p',conversation_id=ready[1])
    store.db_path.unlink()
    with pytest.raises((OperationError, ValueError)):
        service.preview(selection(ready))
    assert not store.db_path.exists()


async def test_project_adoption_survives_personal_library_removal(service, ready):
    import shutil
    shutil.rmtree(ready[2].library.root)
    assert (await create(service, selection(ready)))['contract']['agent']['revision'] == 1


async def test_revoke_parent_child_race_has_no_active_escape(service, ready):
    import asyncio
    parent = await create(service, selection(ready))
    body = selection(ready, parent_id=parent['delegation_id'], scope={'kind':'nodes','node_ids':['a']})
    preview = service.preview(body)
    child_command = command('delegation.create', {**body,'preview_proof':preview['proof']}, 'race-child')
    stop = command('delegation.revoke', {'delegation_id':parent['delegation_id'],'expected_revision':1,'reason':'停止'}, 'stop')
    results = await asyncio.gather(service.apply(child_command), service.apply(stop), return_exceptions=True)
    if not isinstance(results[0], Exception):
        assert service.get(results[0]['delegation_id'])['effective_state'] == 'revoked'
    else:
        assert isinstance(results[0], OperationError)
    assert await service.apply(stop) == results[1]


async def test_child_cannot_cross_session_clear_generation(service, ready, monkeypatch):
    from novelvideo.chat.session_store import SessionState, SessionStore
    parent = await create(service, selection(ready))
    fence = parent['contract']['session_fence']
    original = SessionStore.peek
    def cleared(store):
        state, running = original(store)
        return (SessionState(session_id=state.session_id, generation=fence['generation'] + 1), running)
    monkeypatch.setattr(SessionStore, 'peek', cleared)
    with pytest.raises(OperationError):
        service.preview(selection(ready, parent_id=parent['delegation_id']))
    # History is fixed and is not re-authorized as a new task when queried.
    assert service.get(parent['delegation_id'])['contract']['session_fence'] == fence


async def test_readonly_preview_and_history_do_not_write_business_files(service, ready, ctx):
    import sqlite3
    def snapshot():
        with sqlite3.connect(service.db) as connection:
            dump = '\n'.join(connection.iterdump())
        files = {str(p):p.read_bytes() for p in ctx.state_dir.rglob('*')
                 if p.is_file() and not p.name.endswith(('-shm','-wal'))}
        return dump, files
    before = snapshot()
    service.preview(selection(ready))
    service.main_agent()
    assert service.catalog(ready[0], ready[1]) == []
    assert service.receipt('absent') is None
    assert snapshot() == before


async def test_fixed_asset_reference_requires_registered_slot_and_is_not_arbitrary_url(service, ready):
    with pytest.raises((OperationError, ValueError)):
        service.preview(selection(ready, input_refs=[{'kind':'file','url':'file:///etc/passwd'}]))
    with pytest.raises((OperationError, ValueError)):
        service.preview(selection(ready, input_refs=[{'kind':'asset','asset_ref':{'base_asset_id':'fake','revision':1},
            'slot_id':'character:fake:portrait','role':'portrait'}]))


async def test_preview_respects_canvas_lock_and_never_creates_missing_lock(service, ready, ctx):
    from novelvideo.freezone.canvas_lock import canvas_lock_path, canvas_write_lock
    with canvas_write_lock(ctx.state_dir, ready[0]):
        with pytest.raises(OperationError) as error:
            service.preview(selection(ready))
        assert error.value.code == 'canvas_busy'
    path = canvas_lock_path(ctx.state_dir, ready[0])
    path.unlink()
    with pytest.raises(OperationError) as error:
        service.preview(selection(ready))
    assert error.value.code == 'canvas_busy'
    assert not path.exists()


async def test_registered_asset_input_uses_fixed_archive_after_source_changes(service, ready, ctx):
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.models import NovelCharacter
    from novelvideo.creation.asset_adapter import AssetAdapter
    source = SQLiteStore('p', output_dir=str(ctx.output_dir), state_dir=str(ctx.state_dir))
    await source.initialize()
    try:
        await source.add_character(NovelCharacter(name='Lin'))
        canonical = ctx.output_dir/'assets/characters/Lin/portrait.png'
        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_bytes(b'fixed-first-image')
        adapter = AssetAdapter(source, ObjectStore(ctx.state_dir))
        ref = await adapter.register_base('character', 'Lin')
        body = selection(ready, input_refs=[{'kind':'asset','asset_ref':ref.model_dump(),
            'slot_id':'character:Lin:portrait','role':'portrait'}])
        task = await create(service, body)
        media = task['contract']['inputs'][0]['snapshot']['media']
        archived = ctx.output_dir/media['current_blob']['path']
        assert archived.read_bytes() == b'fixed-first-image'
        canonical.write_bytes(b'new-current-image')
        await adapter.refresh_source(ref.base_asset_id, expected_revision=ref.revision)
        assert service.get(task['delegation_id'])['contract']['inputs'][0]['snapshot']['media'] == media
        assert archived.read_bytes() == b'fixed-first-image'
    finally:
        await source.close()


@pytest.mark.parametrize('kind', ['character', 'scene'])
async def test_asset_context_includes_base_variant_agreements_once_and_freezes_history(service, ready, ctx, kind):
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.models import NovelCharacter, NovelScene, CharacterIdentity
    from novelvideo.creation.asset_adapter import AssetAdapter
    from novelvideo.creation.creative_constraints import CreativeConstraints
    from .test_asset_media import candidate
    from .test_creative_constraints import create as agreement, confirm
    source = SQLiteStore('p', output_dir=str(ctx.output_dir), state_dir=str(ctx.state_dir))
    await source.initialize()
    try:
        if kind == 'character':
            await source.add_character(NovelCharacter(name='Lin'))
            await source.add_character_identity('Lin', CharacterIdentity(identity_id='identity', character_name='Lin', identity_name='coat'))
            base_name, variant_name = 'Lin', 'identity'
            base_slot, variant_slot, role = 'character:Lin:portrait', 'character:Lin:identity:identity:portrait', 'portrait'
            base_kind, variant_kind = 'character_portrait', 'character_portrait'
        else:
            await source.add_scene(NovelScene(name='Hall'))
            await source.add_scene(NovelScene(name='Night', base_scene_id='Hall', variant_id='night'))
            base_name, variant_name = 'Hall', 'Night'
            base_slot, variant_slot, role = 'scene:Hall:base:master', 'scene:Hall:state:Night:master', 'master'
            base_kind, variant_kind = 'scene_base', 'scene_state'
        candidate(source, base_slot, 'base', kind=base_kind, provisional=True)
        variant_path = candidate(source, variant_slot, 'variant', kind=variant_kind, provisional=True)
        adapter = AssetAdapter(source, ObjectStore(ctx.state_dir))
        base = await adapter.register_base(kind, base_name)
        variant = await adapter.register_variant(base.base_asset_id, variant_name)
        constraints = CreativeConstraints(ctx, authority='user')
        base_scope = {'level':'object', 'object_id':base.base_asset_id}
        variant_scope = {'level':'object', 'object_id':variant.variant_id}
        hard = await confirm(constraints, await agreement(constraints, key='identity', value='固定身份', scope=base_scope, request_id='base-fact'), request_id='base-hard')
        await agreement(constraints, key='mood', value='宁静', level='preference', scope=variant_scope, request_id='variant-pref')
        draft = await agreement(CreativeConstraints(ctx), key='guess', value='待核实', scope=variant_scope, request_id='variant-draft')
        asset = lambda ref, slot: {'kind':'asset','asset_ref':ref.model_dump(),'slot_id':slot,'role':role}
        # A variant explicitly references its trusted base identity too. Its scope
        # is resolved independently, not an invented base->variant inheritance.
        variant_only = service.preview(selection(ready, input_refs=[asset(variant, variant_slot)]))
        objects = [item for item in variant_only['contract']['context'] if item['scope']['level'] == 'object']
        assert {item['scope']['object_id'] for item in objects} == {base.base_asset_id, variant.variant_id}
        body = selection(ready, input_refs=[asset(base, base_slot), asset(variant, variant_slot)])
        original = service.preview(body)
        task = await service.apply(command('delegation.create', {**body,'preview_proof':original['proof']}, 'asset-context'))
        objects = [item for item in task['contract']['context'] if item['scope']['level'] == 'object']
        assert len(objects) == 2
        by_id = {item['scope']['object_id']:item['agreements'] for item in objects}
        assert by_id[base.base_asset_id]['effective']['identity']['revision'] == hard['revision']
        assert by_id[variant.variant_id]['effective']['mood']['value'] == '宁静'
        assert by_id[variant.variant_id]['drafts'][0]['constraint_id'] == draft['constraint_id']
        assert by_id[variant.variant_id]['drafts'][0]['level'] == 'draft'
        variant_path.write_bytes(b'new current bytes')
        await adapter.refresh_source(base.base_asset_id, variant.variant_id, expected_revision=variant.revision)
        # Fixed asset versions do not follow later media updates.
        assert service.preview(body)['proof'] == original['proof']
        await constraints.apply(command('constraint.confirm', {'constraint_id':hard['constraint_id'],
            'expected_revision':hard['revision'],'level':'hard','basis':'用户修订','value':'修订后的身份'}, 'change-hard'))
        assert service.preview(body)['proof'] != original['proof']
        with pytest.raises(OperationError):
            await service.apply(command('delegation.create', {**body,'preview_proof':original['proof']}, 'stale-fact'))
        assert service.get(task['delegation_id'])['contract'] == task['contract']
        assert service.receipt('asset-context') == task
    finally:
        await source.close()
