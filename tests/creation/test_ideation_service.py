import json
from types import SimpleNamespace

import pytest

from novelvideo.creation.agent_tools import AgentTools
from novelvideo.creation.ideation_service import IdeationService
from novelvideo.creation.operations import OperationError
from novelvideo.creation.store import ObjectStore
from novelvideo.freezone.canvas_store import canvas_path
from novelvideo.creation import agent_context
from novelvideo.creation.agent_context import node_snapshot
from novelvideo.chat.conversations import ConversationStore
from .test_agent_tools import action, create, selection
from .test_agent_tools import ctx, ready, service, task
from .test_agent_runs import decision, runs as run_service, start_request


def candidates():
    return {'schema_version': 1, 'candidates': [
        {'id': f'idea-{i}', 'title': f'标题{i}', 'premise': f'前提{i}',
         'protagonist_goal': f'目标{i}', 'conflict': f'冲突{i}', 'hook': f'钩子{i}',
         'expansion': f'扩展{i}'} for i in range(1, 4)]}


def body_candidate(identifier='idea-1', **changes):
    return {key: value for key, value in {
        'title': '改后标题', 'premise': '改后前提', 'protagonist_goal': '改后目标',
        'conflict': '改后冲突', 'hook': '改后钩子', 'expansion': '改后扩展',
        **changes}.items()}


def object_ref(ref):
    return {'object_id': ref['id'], 'revision': ref['revision']}


async def create_output(ctx, ready, task, payload=None, *, target_node_ids=None,
                        input_indices=None, request_id='source'):
    canvas_id, conversation_id = ready[:2]

    async def resolve(_role):
        return ctx

    tools = AgentTools(ctx, username='alice', canvas_id=canvas_id,
                       conversation_id=conversation_id,
                       delegation_id=task['delegation_id'], resolve_context=resolve)
    raw = json.dumps(payload or candidates(), ensure_ascii=False)
    result = await tools.execute(action(request_id=request_id, text=raw,
                                         target_node_ids=target_node_ids or ['a'],
                                         input_indices=input_indices or []))
    return result['output']['object_ref'], result['output']


def ideation(ctx, ready):
    canvas_id, conversation_id = ready[:2]

    async def resolve(_role):
        return ctx

    return IdeationService(ctx, username='alice', canvas_id=canvas_id,
                           conversation_id=conversation_id, resolve_context=resolve)


async def adopted_reference(ctx, ready, task, *, candidate_id='idea-2', request_id='adopt-ref'):
    object_ref, _ = await create_output(ctx, ready, task)
    service = ideation(ctx, ready)
    preview = await service.preview({'object_id': object_ref['id'], 'action': 'adopt', 'node_id': 'a',
        'expected_revision': object_ref['revision'], 'candidate_id': candidate_id})
    receipt = await service.adopt(object_ref['id'], {'request_id': request_id, 'node_id': 'a',
        'expected_revision': object_ref['revision'], 'candidate_id': candidate_id, 'proof': preview['proof']})
    ref = {'kind': 'ideation_selection', 'request_id': request_id,
        'canvas_id': ready[0], 'conversation_id': ready[1], 'node_id': 'a',
        'object_id': object_ref['id'], 'revision': object_ref['revision'], 'candidate_id': candidate_id}
    return ref, service, receipt


async def test_reads_only_verified_delegation_output_and_returns_origin_source(ctx, ready, task):
    ref, output = await create_output(ctx, ready, task)
    service = ideation(ctx, ready)

    value = await service.get(ref['id'], node_id='a')

    assert value['object_id'] == ref['id'] and value['revision'] == 1
    assert value['origin'] == object_ref(ref)
    assert value['source']['output_id'] == output['id']
    assert value['source']['operation_id'] == output['operation_id']
    assert value['source']['delegation_id'] == task['delegation_id']
    assert value['source']['run_id'] is None
    assert value['source']['target_node_ids'] == ['a']
    assert len(value['candidates']) == 3 and value['selection'] is None


@pytest.mark.parametrize('change', [{'member_id': 'other-agent'}, {'extra_authority': True}])
async def test_proposal_source_rejects_forged_member_or_extra_authority(ctx, ready, task, change):
    import sqlite3
    ref, output = await create_output(ctx, ready, task)
    output['source'].update(change)
    with sqlite3.connect(ctx.state_dir / 'creation' / 'objects.sqlite3') as conn:
        conn.execute('UPDATE delegation_outputs SET content=? WHERE id=?',
                     (json.dumps(output), output['id']))
    with pytest.raises(OperationError, match='对象不是有效的提案候选输出'):
        await ideation(ctx, ready).get(ref['id'], node_id='a')


async def test_edit_creates_immutable_revision_and_keeps_origin(ctx, ready, task):
    ref, _ = await create_output(ctx, ready, task)
    service = ideation(ctx, ready)
    preview = await service.preview({'object_id': ref['id'], 'action': 'edit', 'node_id': 'a',
        'expected_revision': 1, 'candidate_id': 'idea-1', 'candidate': body_candidate()})
    request = {'request_id': 'edit-1', 'node_id': 'a', 'expected_revision': 1,
        'candidate_id': 'idea-1', 'proof': preview['proof'], 'candidate': body_candidate()}

    result = await service.edit(ref['id'], request)
    current = await service.get(ref['id'], node_id='a')
    historical = await service.get(ref['id'], node_id='a', revision=1)

    assert result['status'] == 'applied' and result['object_ref'] == {'object_id': ref['id'], 'revision': 2}
    assert result['origin'] == object_ref(ref)
    assert current['revision'] == 2 and current['candidates'][0]['title'] == '改后标题'
    assert current['candidates'][1:] == historical['candidates'][1:]
    assert historical['revision'] == 1 and historical['candidates'][0]['title'] == '标题1'


@pytest.mark.parametrize('change', ['edit', 'delete'])
async def test_preview_binds_current_input_snapshot_without_rewriting_source(ctx, ready, task, service, change):
    input_task = await create(service, selection(ready, input_refs=[
        {'kind': 'canvas_node', 'canvas_id': ready[0], 'node_id': 'b'}]), 'input-task')
    ref, _ = await create_output(ctx, ready, input_task, input_indices=[0])
    service = ideation(ctx, ready)
    before = await service.get(ref['id'], node_id='a')
    assert before['source']['inputs'][0]['snapshot']['content'] == {'text': '开场 B'}

    preview = await service.preview({'object_id': ref['id'], 'action': 'edit', 'node_id': 'a',
        'expected_revision': 1, 'candidate_id': 'idea-1', 'candidate': body_candidate()})
    graph_path = canvas_path(ctx.state_dir, ready[0])
    graph = json.loads(graph_path.read_text())
    if change == 'edit':
        next(node for node in graph['nodes'] if node['id'] == 'b')['data']['text'] = '开场 B 已修改'
    else:
        graph['nodes'] = [node for node in graph['nodes'] if node['id'] != 'b']
    graph['revision'] += 1
    graph_path.write_text(json.dumps(graph))

    with pytest.raises(OperationError) as error:
        await service.edit(ref['id'], {'request_id': f'input-{change}', 'node_id': 'a',
            'expected_revision': 1, 'candidate_id': 'idea-1', 'proof': preview['proof'],
            'candidate': body_candidate()})
    assert error.value.code == 'source_conflict'
    assert await service.receipt(f'input-{change}', node_id='a') is None
    after = await service.get(ref['id'], node_id='a')
    assert after['source']['inputs'][0]['snapshot']['content'] == {'text': '开场 B'}


async def test_adoption_is_fixed_to_explicit_object_version_candidate_and_node(ctx, ready, task):
    ref, _ = await create_output(ctx, ready, task)
    service = ideation(ctx, ready)
    preview = await service.preview({'object_id': ref['id'], 'action': 'adopt', 'node_id': 'a',
        'expected_revision': 1, 'candidate_id': 'idea-2'})
    request = {'request_id': 'adopt-1', 'node_id': 'a', 'expected_revision': 1,
        'candidate_id': 'idea-2', 'proof': preview['proof']}

    result = await service.adopt(ref['id'], request)
    await service.edit(ref['id'], {'request_id': 'edit-after-adopt', 'node_id': 'a',
        'expected_revision': 1, 'candidate_id': 'idea-1',
        'proof': (await service.preview({'object_id': ref['id'], 'action': 'edit', 'node_id': 'a',
            'expected_revision': 1, 'candidate_id': 'idea-1', 'candidate': body_candidate()}))['proof'],
        'candidate': body_candidate()})
    current = await service.get(ref['id'], node_id='a')

    assert result['selection'] == {'object_id': ref['id'], 'revision': 1,
        'candidate_id': 'idea-2', 'node_id': 'a', 'run_id': None,
        'source': current['source']}
    assert current['selection'] == result['selection']
    assert current['revision'] == 2 and current['selection']['revision'] == 1

    second_preview = await service.preview({'object_id': ref['id'], 'action': 'adopt', 'node_id': 'a',
        'expected_revision': 2, 'candidate_id': 'idea-3'})
    second = await service.adopt(ref['id'], {'request_id': 'adopt-2', 'node_id': 'a',
        'expected_revision': 2, 'candidate_id': 'idea-3', 'proof': second_preview['proof']})
    latest = await service.get(ref['id'], node_id='a')
    first_receipt = await service.receipt('adopt-1', node_id='a')

    assert latest['selection'] == second['selection']
    assert latest['selection']['revision'] == 2 and latest['selection']['candidate_id'] == 'idea-3'
    assert first_receipt['selection']['revision'] == 1
    assert first_receipt['selection']['candidate_id'] == 'idea-2'


async def test_adoption_preview_expires_when_another_selection_is_committed(ctx, ready, task):
    ref, _ = await create_output(ctx, ready, task)
    service = ideation(ctx, ready)
    old_preview = await service.preview({'object_id': ref['id'], 'action': 'adopt', 'node_id': 'a',
        'expected_revision': 1, 'candidate_id': 'idea-1'})
    first_preview = await service.preview({'object_id': ref['id'], 'action': 'adopt', 'node_id': 'a',
        'expected_revision': 1, 'candidate_id': 'idea-2'})
    await service.adopt(ref['id'], {'request_id': 'adopt-first', 'node_id': 'a',
        'expected_revision': 1, 'candidate_id': 'idea-2', 'proof': first_preview['proof']})

    with pytest.raises(OperationError) as error:
        await service.adopt(ref['id'], {'request_id': 'adopt-stale-preview', 'node_id': 'a',
            'expected_revision': 1, 'candidate_id': 'idea-1', 'proof': old_preview['proof']})
    assert error.value.code == 'source_conflict'


async def test_rejects_arbitrary_story_fragment_and_node_outside_original_targets(ctx, ready, task):
    store = ObjectStore(ctx.state_dir)
    raw = json.dumps(candidates(), ensure_ascii=False)
    arbitrary = store.create('story_fragment', {'title': '伪装', 'text': raw,
        'canvas_id': ready[0], 'episode_id': None}, 'alice')
    service = ideation(ctx, ready)
    with pytest.raises(OperationError):
        await service.get(arbitrary['id'], node_id='a')

    ref, _ = await create_output(ctx, ready, task, target_node_ids=['a'])
    with pytest.raises(OperationError):
        await service.get(ref['id'], node_id='b')


async def test_run_id_is_derived_from_saved_agent_run_rounds(ctx, ready, task, run_service, monkeypatch):
    from novelvideo.creation import agent_model

    async def submit(*_args, **_kwargs):
        return decision(action(text=json.dumps(candidates(), ensure_ascii=False)))

    monkeypatch.setattr(agent_model, 'run_decision', submit)
    run_id = (await run_service.start(start_request(task)))['run']['id']
    await run_service.drive(run_id)
    run = await run_service.get(run_id)
    ref = run['outputs'][0]['object_ref']

    value = await ideation(ctx, ready).get(ref['id'], node_id='a')

    assert value['source']['run_id'] == run_id


async def test_rejects_stale_revision_or_preview_proof(ctx, ready, task):
    ref, _ = await create_output(ctx, ready, task)
    service = ideation(ctx, ready)
    preview = await service.preview({'object_id': ref['id'], 'action': 'edit', 'node_id': 'a',
        'expected_revision': 1, 'candidate_id': 'idea-1', 'candidate': body_candidate()})
    first = {'request_id': 'first-edit', 'node_id': 'a', 'expected_revision': 1,
        'candidate_id': 'idea-1', 'proof': preview['proof'], 'candidate': body_candidate()}
    await service.edit(ref['id'], first)
    with pytest.raises(OperationError):
        await service.edit(ref['id'], {**first, 'request_id': 'stale-edit'})
    with pytest.raises(OperationError):
        await service.edit(ref['id'], {**first, 'request_id': 'forged-proof',
                                       'expected_revision': 2, 'proof': 'stale'})


async def test_edit_is_idempotent_and_request_id_collision_conflicts(ctx, ready, task):
    ref, _ = await create_output(ctx, ready, task)
    service = ideation(ctx, ready)
    preview = await service.preview({'object_id': ref['id'], 'action': 'edit', 'node_id': 'a',
        'expected_revision': 1, 'candidate_id': 'idea-1', 'candidate': body_candidate()})
    request = {'request_id': 'same-request', 'node_id': 'a', 'expected_revision': 1,
        'candidate_id': 'idea-1', 'proof': preview['proof'], 'candidate': body_candidate()}

    first = await service.edit(ref['id'], request)
    assert await service.edit(ref['id'], request) == first
    with pytest.raises(OperationError) as error:
        await service.edit(ref['id'], {**request,
            'candidate': {**body_candidate(), 'title': '其他内容'}})
    assert error.value.code == 'idempotency_conflict'
    assert await service.receipt('same-request', node_id='a') == first


async def test_deleted_node_invalidates_old_preview(ctx, ready, task):
    ref, _ = await create_output(ctx, ready, task)
    service = ideation(ctx, ready)
    preview = await service.preview({'object_id': ref['id'], 'action': 'adopt', 'node_id': 'a',
        'expected_revision': 1, 'candidate_id': 'idea-1'})
    path = canvas_path(ctx.state_dir, ready[0])
    graph = json.loads(path.read_text())
    graph['nodes'] = [node for node in graph['nodes'] if node['id'] != 'a']
    path.write_text(json.dumps(graph))
    with pytest.raises(OperationError):
        await service.adopt(ref['id'], {'request_id': 'after-delete', 'node_id': 'a',
            'expected_revision': 1, 'candidate_id': 'idea-1', 'proof': preview['proof']})


async def test_preview_ignores_layout_but_binds_node_content(ctx, ready, task):
    ref, _ = await create_output(ctx, ready, task)
    service = ideation(ctx, ready)
    request = {'object_id': ref['id'], 'action': 'adopt', 'node_id': 'a',
        'expected_revision': 1, 'candidate_id': 'idea-1'}
    initial = await service.preview(request)
    path = canvas_path(ctx.state_dir, ready[0])
    graph = json.loads(path.read_text())
    node = next(node for node in graph['nodes'] if node['id'] == 'a')
    node.update(position={'x': 300, 'y': 120}, width=600, height=90)
    path.write_text(json.dumps(graph))
    moved = await service.preview(request)
    assert moved['proof'] == initial['proof']

    graph['nodes'][1]['data']['text'] = '正文已修改'
    path.write_text(json.dumps(graph))
    changed = await service.preview(request)
    assert changed['proof'] != initial['proof']


async def test_other_user_cannot_read_an_owned_delegation_output(ctx, ready, task):
    ref, _ = await create_output(ctx, ready, task)
    other = SimpleNamespace(**{**vars(ctx), 'requester_user_id': 'bob', 'owner_username': 'bob'})

    async def resolve(_role):
        return other

    service = IdeationService(other, username='bob', canvas_id=ready[0],
        conversation_id=ready[1], resolve_context=resolve)
    with pytest.raises(OperationError):
        await service.get(ref['id'], node_id='a')


async def test_read_does_not_create_domain_schema_or_receipt(ctx, ready, task):
    ref, _ = await create_output(ctx, ready, task)
    service = ideation(ctx, ready)

    with service.read() as conn:
        before = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert await service.get(ref['id'], node_id='a')
    with service.read() as conn:
        after = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        receipt = conn.execute("SELECT 1 FROM creative_domain_receipts WHERE domain='ideation'").fetchone()
    assert after == before and receipt is None


async def test_agent_delegation_input_resolves_only_the_adopted_candidate(ctx, ready, task, service):
    ref, _ideation, receipt = await adopted_reference(ctx, ready, task)
    body = selection(ready, input_refs=[ref])
    frozen = await create(service, body, 'adopted-candidate-input')
    item = frozen['contract']['inputs'][0]

    assert item['ref'] == ref
    assert item['snapshot']['candidate'] == next(
        candidate for candidate in candidates()['candidates'] if candidate['id'] == 'idea-2')
    assert set(item['snapshot']['candidate']) == {
        'id', 'title', 'premise', 'protagonist_goal', 'conflict', 'hook', 'expansion'}
    assert 'candidates' not in item['snapshot']
    assert item['snapshot']['provenance']['request_id'] == receipt['request_id']
    assert item['snapshot']['provenance']['source'] == receipt['selection']['source']
    assert 'execution_enabled' not in item['snapshot']
    assert agent_context.required_canvases(ctx, None, body) == {ready[0]}


async def test_agent_tools_revalidates_fixed_ideation_input_before_execution(ctx, ready, task, service):
    ref, _ideation, _receipt = await adopted_reference(ctx, ready, task,
                                                       request_id='adopt-agent-tools-input')
    delegated = await create(service, selection(ready, input_refs=[ref]), 'agent-tools-input-task')
    _object_ref, output = await create_output(ctx, ready, delegated, input_indices=[0],
                                               request_id='proposal-with-adopted-input')

    assert output['operation'] == 'proposal.create'
    assert output['input_indices'] == [0]
    assert output['target_node_ids'] == ['a']


@pytest.mark.parametrize('forgery', [
    'request_id', 'canvas_id', 'conversation_id', 'node_id', 'object_id',
    'revision', 'candidate_id', 'actor', 'project',
])
async def test_forged_ideation_selection_is_rejected(ctx, ready, task, forgery):
    ref, source, _receipt = await adopted_reference(ctx, ready, task,
                                                    request_id=f'adopt-forgery-{forgery}')
    fake_ref = dict(ref)
    fake_ctx = ctx
    if forgery in {'request_id', 'canvas_id', 'conversation_id', 'node_id', 'object_id',
                   'revision', 'candidate_id'}:
        fake_ref[forgery] = {'request_id': 'missing-request', 'revision': 99}.get(
            forgery, f'forged-{forgery}')
    elif forgery == 'actor':
        fake_ctx = SimpleNamespace(**{**vars(ctx), 'requester_user_id': 'mallory'})
    else:
        fake_ctx = SimpleNamespace(**{**vars(ctx), 'project_id': 'another-project'})

    with source.read() as conn, pytest.raises(OperationError):
        agent_context.inputs(fake_ctx, conn, [fake_ref], username='alice',
                             current_canvas_id=ready[0])


async def test_old_selection_stays_fixed_after_edit_and_readoption(ctx, ready, task):
    old_ref, service, old_receipt = await adopted_reference(ctx, ready, task,
                                                             request_id='adopt-before-edits')
    edit = body_candidate(title='编辑后的候选二')
    preview = await service.preview({'object_id': old_ref['object_id'], 'action': 'edit', 'node_id': 'a',
        'expected_revision': 1, 'candidate_id': 'idea-2', 'candidate': edit})
    await service.edit(old_ref['object_id'], {'request_id': 'edit-after-adoption', 'node_id': 'a',
        'expected_revision': 1, 'candidate_id': 'idea-2', 'proof': preview['proof'], 'candidate': edit})
    next_preview = await service.preview({'object_id': old_ref['object_id'], 'action': 'adopt',
        'node_id': 'a', 'expected_revision': 2, 'candidate_id': 'idea-3'})
    await service.adopt(old_ref['object_id'], {'request_id': 'readopt-after-edit', 'node_id': 'a',
        'expected_revision': 2, 'candidate_id': 'idea-3', 'proof': next_preview['proof']})

    with service.read() as conn:
        frozen = agent_context.inputs(ctx, conn, [old_ref], username='alice',
                                      current_canvas_id=ready[0])[0]
    assert frozen['snapshot']['candidate']['title'] == '标题2'
    assert frozen['snapshot']['provenance']['request_id'] == old_receipt['request_id']


async def test_adopted_reference_can_cross_conversations_on_the_same_canvas(ctx, ready, task, service):
    ref, _ideation, receipt = await adopted_reference(ctx, ready, task,
                                                       request_id='adopt-old-conversation')
    current = ConversationStore('alice', 'project', 'p').create(
        '新的画布会话', canvas_id=ready[0], request_id='new-canvas-conversation')
    body = selection(ready, conversation_id=current['id'], input_refs=[ref])
    preview = service.preview(body)

    assert ref['conversation_id'] != current['id']
    assert preview['contract']['conversation_id'] == current['id']
    assert preview['contract']['inputs'][0]['snapshot']['provenance']['conversation_id'] == ref['conversation_id']
    assert preview['contract']['inputs'][0]['snapshot']['provenance']['request_id'] == receipt['request_id']


async def test_adopted_reference_rejects_a_deleted_source_node(ctx, ready, task):
    ref, service, _receipt = await adopted_reference(ctx, ready, task,
                                                     request_id='adopt-delete-node')
    path = canvas_path(ctx.state_dir, ready[0])
    graph = json.loads(path.read_text())
    graph['nodes'] = [node for node in graph['nodes'] if node['id'] != ref['node_id']]
    path.write_text(json.dumps(graph))

    with service.read() as conn, pytest.raises(OperationError) as error:
        agent_context.inputs(ctx, conn, [ref], username='alice', current_canvas_id=ready[0])
    assert error.value.code == 'scope_violation'
    assert '节点已删除' in str(error.value)


def test_agent_node_runtime_metadata_does_not_change_semantic_snapshot():
    node = {'id': 'agent', 'type': 'agentNode', 'data': {
        'agentRef': {'id': 'builtin:ideation', 'revision': 1},
        'settings': {'genrePrimary': '悬疑', 'idea': '雨夜站台'},
        'conversationId': 'conversation-1', 'runRefs': [], 'adoptedRef': None,
    }}
    before = node_snapshot(node)
    runtime_update = {'id': 'agent', 'type': 'agentNode', 'data': {
        **node['data'], 'conversationId': 'conversation-2',
        'runRefs': [{'id': 'run-1', 'revision': 3, 'state': 'complete',
                     'inputSignature': 'signature', 'delegationId': 'delegation'}],
        'adoptedRef': {'kind': 'ideation_selection', 'request_id': 'adopt',
                       'canvas_id': 'canvas', 'conversation_id': 'conversation-1',
                       'node_id': 'agent', 'object_id': 'object', 'revision': 1,
                       'candidate_id': 'idea-1'},
    }}

    after_runtime_update = node_snapshot(runtime_update)
    changed_settings = {'id': 'agent', 'type': 'agentNode', 'data': {
        **runtime_update['data'], 'settings': {'genrePrimary': '喜剧', 'idea': '雨夜站台'},
    }}
    after_settings_change = node_snapshot(changed_settings)

    assert before == after_runtime_update
    assert before['content']['agentRef'] == node['data']['agentRef']
    assert before['content']['settings'] == node['data']['settings']
    assert after_settings_change['input_fingerprint'] != before['input_fingerprint']
    assert after_settings_change['content']['settings']['genrePrimary'] == '喜剧'
