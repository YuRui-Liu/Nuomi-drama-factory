import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import creation as discussion_routes
from novelvideo.api.routes import creation_library as task_routes
from novelvideo.creation import agent_model, discussions
from novelvideo.creation import agent_runs as run_module
from novelvideo.creation.agent_runs import AgentRuns
from .test_delegations import ctx, ready, service
from .test_ontology import command


@pytest.fixture
async def api(ctx, monkeypatch):
    user = {'username': 'alice', 'id': 'alice'}

    async def context(project, principal, role):
        if project != 'p' or principal.get('id') == 'denied':
            raise HTTPException(403)
        if role == 'editor' and principal.get('id') == 'viewer':
            raise HTTPException(403)
        return SimpleNamespace(**{**vars(ctx), 'requester_user_id': principal['id']})

    monkeypatch.setattr(discussion_routes, 'context_for', context)
    monkeypatch.setattr(task_routes, 'context_for', context)
    monkeypatch.setattr(discussions, 'availability', lambda _: {
        'available': True,
        'code': 'configured_not_verified',
        'route': {'runtime': 'model_api', 'task_role': discussions.ROLE, 'model': 'test-model'},
        'configuration_fingerprint': 'config-1',
    })
    monkeypatch.setattr(agent_model, 'availability', lambda _: {
        'available': True, 'code': 'configured_not_verified',
        'configuration_fingerprint': 'config-1',
        'route': {'runtime': 'model_api', 'model': 'test-model',
                  'task_role': 'creation_agent_decision', 'source': 'global'},
    })
    app = FastAPI()
    app.include_router(discussion_routes.router)
    app.include_router(task_routes.router)
    app.dependency_overrides[get_api_user] = lambda: dict(user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        yield client, user


def _body(ready, **changes):
    return {
        'request_id': 'chat-task-1',
        'canvas_id': ready[0],
        'conversation_id': ready[1],
        'message': '请整理当前片段的悬疑开场，并给我两个可选方向。',
        'selection': {'kind': 'nodes', 'node_ids': ['a', 'b']},
        **changes,
    }


async def _decision(*actions, delegations=None):
    return {
        'decision': {'message': '已整理候选方向。',
                     'actions': [{'operation': item['operation'], 'payload': item['payload']}
                                 for item in actions],
                     'delegations': delegations or [],
                     'finished': True, 'unmet': []},
        'usage': {'input_tokens': 50, 'output_tokens': 20, 'response_received': True},
        'cost': {'status': 'unknown', 'amount': None},
    }


async def test_reply_intent_never_creates_a_task_or_run(api, ready, monkeypatch):
    client, _ = api

    async def intake(*args, **kwargs):
        return {'intent': 'reply', 'message': '可以从人物动机与场景目标两方面讨论。'}

    monkeypatch.setattr(discussions, 'run_message', intake)
    monkeypatch.setattr(AgentRuns, 'start', lambda *a, **k: pytest.fail('reply must not start a run'))

    response = await client.post('/projects/p/creation/discussions', json=_body(ready))

    assert response.status_code == 200, response.text
    assert response.json()['data']['message'] == '可以从人物动机与场景目标两方面讨论。'
    assert response.json()['data'].get('collaboration_task') is None
    tasks = await client.get('/projects/p/creation/collaboration-tasks', params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    })
    assert tasks.status_code == 200, tasks.text
    assert tasks.json()['data'] == []


async def test_agent_plan_preview_is_persisted_but_does_not_start_a_run(api, ready, service, monkeypatch):
    """Planning is a review step; only the separate confirm endpoint may start work."""
    from novelvideo.creation.builtin_agents import catalog
    from .test_ontology import command

    client, _ = api
    lead = next(item for item in catalog() if item['id'] == 'builtin:creative-lead')
    adopted = await ready[2].apply(command('adopt', {
        'source': 'builtin', 'ref': lead['source']['ref'], 'proof': lead['proof'],
    }, 'plan-preview-lead'))
    await service.apply(command('main.select', {
        'expected_binding_revision': 1, 'adoption_id': adopted['adoption_id'],
        'expected_adoption_revision': adopted['revision'],
    }, 'plan-preview-select-lead'))

    async def intake(*args, **kwargs):
        return {'intent': 'prepare', 'message': '整理悬疑开场候选。',
                'acceptance': ['形成一个可比较的开场候选。']}

    monkeypatch.setattr(discussions, 'run_message', intake)
    monkeypatch.setattr(AgentRuns, 'start_with_pinned_specialists',
                        lambda *a, **k: pytest.fail('preview must not start an Agent run'))

    response = await client.post('/projects/p/creation/agent-plans/preview', json={
        'request_id': 'plan-preview-1', 'canvas_id': ready[0],
        'conversation_id': ready[1], 'message': '整理悬疑开场候选。',
        'selection': {'kind': 'nodes', 'node_ids': ['a', 'b']},
    })

    assert response.status_code == 200, response.text
    plan = response.json()['data']
    assert plan['state'] == 'draft'
    assert plan['revision'] == 1
    assert plan['scope']['node_ids'] == ['a', 'b']
    assert plan['tasks'][0]['depends_on'] == []
    assert plan['tasks'][0]['permission'] == 'text'
    assert plan['tasks'][0]['role']['adoption_id'] == adopted['adoption_id']
    assert plan['tasks'][0]['skills']
    assert plan['frozen_inputs']
    assert plan['tasks'][0]['outputs']
    task_list = await client.get('/projects/p/creation/collaboration-tasks', params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    })
    assert task_list.json()['data'] == []
    from .test_delegations import edit_graph
    edit_graph(ready[2].ctx, ready[0], lambda graph: graph['nodes'][0]['data'].update({'text': 'changed'}))
    stale = await client.post(f"/projects/p/creation/agent-plans/{plan['id']}/confirm", params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    }, json={'request_id': 'plan-confirm-stale', 'expected_revision': 1,
             'confirmed': True, 'permission': 'text'})
    assert stale.status_code == 409
    assert stale.json()['detail']['code'] == 'stale_input'


async def test_agent_plan_edit_and_text_confirm_are_idempotent_and_media_safe(api, ready, service, monkeypatch):
    from novelvideo.creation.builtin_agents import catalog
    from .test_ontology import command

    client, _ = api
    lead = next(item for item in catalog() if item['id'] == 'builtin:creative-lead')
    adopted = await ready[2].apply(command('adopt', {
        'source': 'builtin', 'ref': lead['source']['ref'], 'proof': lead['proof'],
    }, 'plan-confirm-lead'))
    await service.apply(command('main.select', {
        'expected_binding_revision': 1, 'adoption_id': adopted['adoption_id'],
        'expected_adoption_revision': adopted['revision'],
    }, 'plan-confirm-select-lead'))

    async def intake(*args, **kwargs):
        return {'intent': 'prepare', 'message': '初始计划', 'acceptance': ['有一个候选。']}

    monkeypatch.setattr(discussions, 'run_message', intake)
    preview = await client.post('/projects/p/creation/agent-plans/preview', json={
        'request_id': 'plan-edit-preview', 'canvas_id': ready[0],
        'conversation_id': ready[1], 'message': '初始计划',
        'selection': {'kind': 'nodes', 'node_ids': ['a']},
    })
    assert preview.status_code == 200, preview.text
    plan = preview.json()['data']
    edited = await client.put(f"/projects/p/creation/agent-plans/{plan['id']}", params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    }, json={
        'request_id': 'plan-edit-1', 'expected_revision': 1,
        'tasks': [{**plan['tasks'][0], 'goal': '改写后的任务目标'}],
    })
    assert edited.status_code == 200, edited.text
    assert edited.json()['data']['revision'] == 2
    assert edited.json()['data']['tasks'][0]['goal'] == '改写后的任务目标'

    media_plan = edited.json()['data']
    media_plan['tasks'][0]['permission'] = 'media'
    media_plan['tasks'][0]['outputs'] = [{'type': 'image', 'target': 'canvas'}]
    rejected = await client.put(f"/projects/p/creation/agent-plans/{plan['id']}", params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    }, json={
        'request_id': 'plan-edit-media', 'expected_revision': 2,
        'tasks': media_plan['tasks'],
    })
    assert rejected.status_code == 200, rejected.text
    denied = await client.post(f"/projects/p/creation/agent-plans/{plan['id']}/confirm", params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    }, json={
        'request_id': 'plan-confirm-media', 'expected_revision': 3,
        'confirmed': True, 'permission': 'text',
    })
    assert denied.status_code == 409
    assert denied.json()['detail']['code'] == 'media_confirmation_required'

    image_output_plan = [{**plan['tasks'][0],
                          'outputs': [{'type': 'image', 'target': 'canvas'}]}]
    image_output_edit = await client.put(f"/projects/p/creation/agent-plans/{plan['id']}", params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    }, json={
        'request_id': 'plan-edit-image-output', 'expected_revision': 3,
        'tasks': image_output_plan,
    })
    assert image_output_edit.status_code == 200, image_output_edit.text
    image_output_denied = await client.post(
        f"/projects/p/creation/agent-plans/{plan['id']}/confirm", params={
            'canvas_id': ready[0], 'conversation_id': ready[1],
        }, json={
            'request_id': 'plan-confirm-image-output', 'expected_revision': 4,
            'confirmed': True, 'permission': 'text',
        })
    assert image_output_denied.status_code == 409
    assert image_output_denied.json()['detail']['code'] == 'media_confirmation_required'

    async def agent_decision(*args, **kwargs):
        return await _decision()

    monkeypatch.setattr(agent_model, 'run_decision', agent_decision)

    restored = await client.put(f"/projects/p/creation/agent-plans/{plan['id']}", params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    }, json={
        'request_id': 'plan-edit-text', 'expected_revision': 4,
        'tasks': [{**plan['tasks'][0], 'goal': '确认后的任务目标'}],
    })
    assert restored.status_code == 200, restored.text
    confirmed = await client.post(f"/projects/p/creation/agent-plans/{plan['id']}/confirm", params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    }, json={
        'request_id': 'plan-confirm-text', 'expected_revision': 5,
        'confirmed': True, 'permission': 'text',
    })
    assert confirmed.status_code == 200, confirmed.text
    run_id = confirmed.json()['data']['run_id']
    task_response = await client.get(f"/projects/p/creation/collaboration-tasks/{confirmed.json()['data']['task_id']}", params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    })
    assert task_response.status_code == 200, task_response.text
    assert '确认后的任务目标' in task_response.json()['data']['goal']
    replay = await client.post(f"/projects/p/creation/agent-plans/{plan['id']}/confirm", params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    }, json={
        'request_id': 'plan-confirm-text', 'expected_revision': 5,
        'confirmed': True, 'permission': 'text',
    })
    assert replay.status_code == 200, replay.text
    assert replay.json()['data']['run_id'] == run_id


async def test_agent_plan_accepts_valid_dag_rejects_cycles_and_launches_one_idempotent_contract(
        api, ready, service, monkeypatch):
    from novelvideo.creation.builtin_agents import catalog
    from .test_ontology import command

    client, _ = api
    lead = next(item for item in catalog() if item['id'] == 'builtin:creative-lead')
    adopted = await ready[2].apply(command('adopt', {
        'source': 'builtin', 'ref': lead['source']['ref'], 'proof': lead['proof'],
    }, 'plan-dag-lead'))
    await service.apply(command('main.select', {
        'expected_binding_revision': 1, 'adoption_id': adopted['adoption_id'],
        'expected_adoption_revision': adopted['revision'],
    }, 'plan-dag-select-lead'))

    async def intake(*args, **kwargs):
        return {'intent': 'prepare', 'message': '制作开场文本计划', 'acceptance': ['产出可审阅的文本。']}

    monkeypatch.setattr(discussions, 'run_message', intake)
    preview = await client.post('/projects/p/creation/agent-plans/preview', json={
        'request_id': 'plan-dag-preview', 'canvas_id': ready[0],
        'conversation_id': ready[1], 'message': '制作开场文本计划',
        'selection': {'kind': 'nodes', 'node_ids': ['a']},
    })
    assert preview.status_code == 200, preview.text
    plan = preview.json()['data']
    base = plan['tasks'][0]
    tasks = [
        {**base, 'task_key': 'research', 'goal': '梳理开场人物动机', 'depends_on': []},
        {**base, 'task_key': 'draft', 'goal': '根据人物动机撰写开场', 'depends_on': ['research']},
        {**base, 'task_key': 'review', 'goal': '检查开场节奏', 'depends_on': ['draft']},
    ]
    missing = [{**tasks[0], 'depends_on': ['absent']}, *tasks[1:]]
    rejected_missing = await client.put(f"/projects/p/creation/agent-plans/{plan['id']}", params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    }, json={'request_id': 'plan-dag-missing', 'expected_revision': 1, 'tasks': missing})
    assert rejected_missing.status_code == 409
    assert rejected_missing.json()['detail']['code'] == 'invalid_input'

    cyclic = [{**tasks[0], 'depends_on': ['review']}, *tasks[1:]]
    rejected_cycle = await client.put(f"/projects/p/creation/agent-plans/{plan['id']}", params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    }, json={'request_id': 'plan-dag-cycle', 'expected_revision': 1, 'tasks': cyclic})
    assert rejected_cycle.status_code == 409
    assert rejected_cycle.json()['detail']['code'] == 'invalid_input'

    edited = await client.put(f"/projects/p/creation/agent-plans/{plan['id']}", params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    }, json={'request_id': 'plan-dag-valid', 'expected_revision': 1, 'tasks': tasks})
    assert edited.status_code == 200, edited.text
    assert edited.json()['data']['tasks'][1]['depends_on'] == ['research']
    task_list = await client.get('/projects/p/creation/collaboration-tasks', params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    })
    assert task_list.json()['data'] == []

    async def agent_decision(*args, **kwargs):
        return await _decision()

    monkeypatch.setattr(agent_model, 'run_decision', agent_decision)
    confirmed = await client.post(f"/projects/p/creation/agent-plans/{plan['id']}/confirm", params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    }, json={'request_id': 'plan-dag-confirm', 'expected_revision': 2,
             'confirmed': True, 'permission': 'text'})
    assert confirmed.status_code == 200, confirmed.text
    task = await client.get(f"/projects/p/creation/collaboration-tasks/{confirmed.json()['data']['task_id']}",
                            params={'canvas_id': ready[0], 'conversation_id': ready[1]})
    assert task.status_code == 200, task.text
    assert '梳理开场人物动机' in task.json()['data']['goal']
    assert '根据人物动机撰写开场' in task.json()['data']['goal']
    assert '检查开场节奏' in task.json()['data']['goal']
    replay = await client.post(f"/projects/p/creation/agent-plans/{plan['id']}/confirm", params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    }, json={'request_id': 'plan-dag-confirm', 'expected_revision': 2,
             'confirmed': True, 'permission': 'text'})
    assert replay.status_code == 200, replay.text
    assert replay.json()['data']['run_id'] == confirmed.json()['data']['run_id']


async def test_prepare_intent_creates_a_real_pinned_agent_run_without_old_form(api, ready, service, monkeypatch):
    from novelvideo.creation.builtin_agents import catalog, resolve
    from .test_agent_tools import action

    client, _ = api
    profiles = ready[2]
    lead = next(item for item in catalog() if item['id'] == 'builtin:creative-lead')
    adopted = await profiles.apply(command('adopt', {
        'source': 'builtin', 'ref': lead['source']['ref'], 'proof': lead['proof'],
    }, 'collaboration-builtin-lead'))
    character = resolve({'id': 'builtin:character-designer', 'revision': 1})
    adopted_character = await profiles.apply(command('adopt', {
        'source': 'builtin', 'ref': character['source']['ref'], 'proof': character['proof'],
    }, 'collaboration-builtin-character'))
    await service.apply(command('main.select', {
        'expected_binding_revision': 1, 'adoption_id': adopted['adoption_id'],
        'expected_adoption_revision': adopted['revision'],
    }, 'collaboration-select-lead'))
    from novelvideo.creation.collaboration import Collaboration
    Collaboration(service.ctx, username='alice', canvas_id=ready[0],
                  conversation_id=ready[1]).join(adopted_character['adoption_id'], adopted_character['revision'])
    intake_calls = []

    async def intake(*args, **kwargs):
        intake_calls.append(kwargs['request_id'])
        return {'intent': 'prepare', 'message': '我会整理两个可比较的方向。',
                'acceptance': ['给出两个有差异的悬疑开场候选。']}

    monkeypatch.setattr(discussions, 'run_message', intake)
    child_plan = {
        'task_key': 'character-direction', 'adoption_id': adopted_character['adoption_id'],
        'target_node_ids': ['a', 'b'], 'input_indices': [],
        'allowed_operations': ['proposal.create'],
        'goal': '整理两个可比较的悬疑开场方向',
        'acceptance': ['给出两个有差异的悬疑开场候选。'],
    }

    async def run_agent(_ctx, _route, prompt, **_identity):
        goal = json.loads(prompt)['goal']
        if goal == _body(ready)['message']:
            return await _decision(delegations=[child_plan])
        return await _decision(action(title='开场方向', text='两人在空站台发现同一封信。',
                                       target_node_ids=['a', 'b']))

    monkeypatch.setattr(agent_model, 'run_decision', run_agent)
    response = await client.post('/projects/p/creation/discussions', json=_body(ready))

    assert response.status_code == 200, response.text
    result = response.json()['data']
    task = result['collaboration_task']
    assert task['state'] == 'queued'
    assert task['source_request_id'] == 'chat-task-1'
    assert task['speaker']['adoption_id'] == adopted['adoption_id']
    assert task['goal'] == '请整理当前片段的悬疑开场，并给我两个可选方向。'
    assert task['user_message'] == _body(ready)['message']
    assert task['goal'] == task['user_message'] != result['message']
    assert task['acceptance'] == ['给出两个有差异的悬疑开场候选。']
    assert task['max_model_calls'] == 8
    assert task['scope']['node_ids'] == ['a', 'b']
    assert task['delegation_id'] and task['run_id']
    task_detail = (await client.get(f"/projects/p/creation/collaboration-tasks/{task['id']}", params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    })).json()['data']
    assert task_detail['state'] == 'delivered'
    run = (await client.get(f"/projects/p/creation/agent-runs/{task['run_id']}", params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    })).json()['data']
    assert run['state'] == 'delivered'
    assert run['rounds'][0]['decision']['actions'] == []
    assert run['children'][0]['task_key'] == 'character-direction'
    assert run['children'][0]['state'] == 'delivered'
    child_delegation = (await client.get(
        f"/projects/p/creation/delegations/{run['children'][0]['delegation_id']}", params={
            'canvas_id': ready[0], 'conversation_id': ready[1],
        })).json()['data']
    assert child_delegation['contract']['agent']['adoption_id'] == adopted_character['adoption_id']
    assert child_delegation['contract']['agent']['revision'] == adopted_character['revision']
    assert run['specialists'][0]['adoption_id'] == adopted_character['adoption_id']
    assert run['specialists'][0]['revision'] == adopted_character['revision']
    delegation = (await client.get(f"/projects/p/creation/delegations/{task['delegation_id']}", params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    })).json()['data']
    assert delegation['contract']['task_brief'] == task['user_message']
    assert 'asset.design.create' in delegation['contract']['allowed_operations']
    assert set(delegation['contract']['allowed_operations']) <= {
        'context.read', 'proposal.create', 'ontology.definition.draft', 'media.propose',
        'asset.design.create',
    }
    assert intake_calls == ['chat-task-1']
    replay = await client.post('/projects/p/creation/discussions', json=_body(ready))
    assert replay.status_code == 200, replay.text
    assert replay.json()['data']['collaboration_task']['run_id'] == task['run_id']
    assert intake_calls == ['chat-task-1']


async def test_formal_script_prepare_without_script_node_runs_writer_then_fixed_version_reviewer(
        api, ctx, ready, service, monkeypatch):
    from novelvideo.chat.conversations import ConversationStore
    from novelvideo.creation.builtin_agents import catalog
    from novelvideo.creation.canvas_workspaces import CanvasCreate, CanvasWorkspace
    from novelvideo.creation.collaboration import Collaboration
    from novelvideo.creation.projection import ProjectionService
    from novelvideo.creation.store import ObjectStore
    from novelvideo.freezone.canvas_store import read_canvas

    client, _ = api
    profiles = ready[2]
    objects = ObjectStore(ctx.state_dir)
    created = CanvasWorkspace(objects, project_id='p').create(
        'alice', CanvasCreate(request_id='formal-script-concept', kind='concept', title='正式剧本'))
    ProjectionService(objects).apply(created.operation_id)
    canvas_id = created.object_refs[0]['canvas_id']
    conversation = ConversationStore('alice', 'project', 'p').create(
        '正式剧本讨论', canvas_id=canvas_id, request_id='formal-script-conversation')
    conversation_id = conversation['id']

    async def adopt_builtin(agent_id, request_id):
        item = next(value for value in catalog() if value['id'] == agent_id)
        adopted = await profiles.apply(command('adopt', {
            'source': 'builtin', 'ref': item['source']['ref'], 'proof': item['proof'],
        }, request_id))
        return adopted

    lead = await adopt_builtin('builtin:creative-lead', 'formal-script-lead')
    writer = await adopt_builtin('builtin:text-writer', 'formal-script-writer')
    reviewer = await adopt_builtin('builtin:script-editor', 'formal-script-reviewer')
    await service.apply(command('main.select', {
        'expected_binding_revision': 1, 'adoption_id': lead['adoption_id'],
        'expected_adoption_revision': lead['revision'],
    }, 'formal-script-select-lead'))
    collaboration = Collaboration(service.ctx, username='alice', canvas_id=canvas_id,
                                  conversation_id=conversation_id)
    collaboration.join(writer['adoption_id'], writer['revision'])
    collaboration.join(reviewer['adoption_id'], reviewer['revision'])

    initial_graph = read_canvas(ctx.state_dir, canvas_id)
    assert not any(item['type'] == 'scriptNode' for item in initial_graph['nodes'])
    user_message = (
        '请主创调度主笔和剧本编辑，为《最后一盏灯》完成正式第1集剧本：先由主笔真正保存正式 '
        'episode_script 草案，再由独立剧本编辑读取该固定草案并审阅。目标约90秒，雨夜停电的老居民楼门厅，'
        '仅林舟和陈奶奶两名可见人物；不改现有资产节点，不调用美术或图片生成。'
    )
    intake_prompts = []

    async def intake(_ctx, _route, prompt, *, request_id):
        payload = json.loads(prompt)
        system = discussions._system_prompt_for_prompt(prompt)
        intake_prompts.append((payload, system, request_id))
        return {
            'intent': 'prepare', 'preparation_kind': 'formal_episode_script',
            'message': '我会安排主笔保存正式剧本草案，再由独立编辑审阅。',
            'acceptance': ['正式第1集剧本草案已保存，并由另一位剧本编辑审阅固定版本。'],
        }

    children = [
        {'task_key': 'writer', 'scope_kind': 'canvas', 'adoption_id': writer['adoption_id'],
         'target_node_ids': [], 'input_indices': [],
         'allowed_operations': ['script.document.draft'],
         'goal': 'writer episode script', 'acceptance': ['保存完整正式第1集草案。']},
        {'task_key': 'reviewer', 'scope_kind': 'canvas', 'adoption_id': reviewer['adoption_id'],
         'target_node_ids': [], 'input_indices': [],
         'allowed_operations': ['script.document.review'],
         'goal': 'review fixed episode script', 'acceptance': ['审阅指定固定剧本版本。'],
         'depends_on': ['writer'],
         'input_output_selections': [{'source_task_key': 'writer', 'output_index': 0}]},
    ]
    reviewer_inputs = []
    writer_draft_calls = 0
    writer_draft_submissions = 0

    async def run_agent(_ctx, _route, prompt, **_identity):
        nonlocal writer_draft_calls, writer_draft_submissions
        value = json.loads(prompt)
        if value['goal'] == user_message:
            assert 'node.run' not in value['allowed_operations']
            assert value['allowed_operations'] == ['context.read']
            assert value['delegation_policy']['delegable_operations'] == [
                'script.document.draft', 'script.document.review']
            assert 'dedicated writer' in value['delegation_policy']['instruction']
            return await _decision(delegations=children)
        if value['goal'] == 'writer episode script':
            writer_draft_calls += 1
            if writer_draft_calls > 1:
                return await _decision()
            writer_draft_submissions += 1
            result = await _decision({
                'operation': 'script.document.draft',
                'payload': {
                    'target_node_ids': [], 'input_indices': [], 'acceptance_indices': [0],
                    'episode_number': 1, 'title': '第一集：最后一盏灯',
                    'markdown': '# 第一集：最后一盏灯\n\n## 1-1 雨夜·老居民楼门厅\n林舟先确认断电，再安全检修并点亮门厅旧灯。\n',
                },
            })
            result['decision']['finished'] = False
            return result
        if value['goal'] == 'review fixed episode script':
            reviewer_inputs.append(value['inputs'])
            return await _decision({
                'operation': 'script.document.review',
                'payload': {
                    'target_node_ids': [], 'input_indices': [0], 'acceptance_indices': [0],
                    'document_input_index': 0,
                    'report': {'conclusion': 'passed', 'summary': '因果与安全动作清楚。', 'issues': []},
                },
            })
        return await _decision()

    monkeypatch.setattr(discussions, 'run_message', intake)
    monkeypatch.setattr(agent_model, 'run_decision', run_agent)
    response = await client.post('/projects/p/creation/discussions', json=_body(
        (canvas_id, conversation_id), message=user_message, selection={'kind': 'canvas'}))

    assert response.status_code == 200, response.text
    task = response.json()['data']['collaboration_task']
    assert task['intent'] == 'prepare'
    assert task['preparation_kind'] == 'formal_episode_script'
    assert task['state'] == 'queued'
    assert len(intake_prompts) == 1
    payload, system, request_id = intake_prompts[0]
    assert request_id == 'chat-task-1'
    assert 'scriptNode' not in payload['canvas_text']
    assert 'preparation_kind 为 formal_episode_script' in system
    assert '不得根据该字段虚构节点 ID' in system

    task_detail = (await client.get(f"/projects/p/creation/collaboration-tasks/{task['id']}", params={
        'canvas_id': canvas_id, 'conversation_id': conversation_id,
    })).json()['data']
    assert task_detail['state'] == 'delivered', json.dumps({
        'error_code': task_detail.get('error_code'),
        'error': task_detail.get('run', {}).get('error'),
        'unmet': task_detail.get('run', {}).get('unmet'),
        'unmet_by_task': task_detail.get('run', {}).get('unmet_by_task'),
        'children': task_detail.get('run', {}).get('children'),
    }, ensure_ascii=False)
    writer_run = (await client.get(
        f"/projects/p/creation/agent-runs/{task_detail['run']['children'][0]['child_run_id']}",
        params={'canvas_id': canvas_id, 'conversation_id': conversation_id},
    )).json()['data']
    assert task_detail['run']['state'] == 'delivered'
    assert [item['state'] for item in task_detail['run']['children']] == ['delivered', 'delivered']
    assert writer_draft_submissions == 1
    assert len(reviewer_inputs) == 1
    fixed_script = next(item for item in reviewer_inputs[0]
                        if item['ref'].get('kind') == 'script_document')
    assert fixed_script['snapshot']['kind'] == 'episode_script'
    assert fixed_script['snapshot']['episode_number'] == 1
    assert fixed_script['snapshot']['markdown'].startswith('# 第一集：最后一盏灯')

    root_delegation = (await client.get(
        f"/projects/p/creation/delegations/{task['delegation_id']}",
        params={'canvas_id': canvas_id, 'conversation_id': conversation_id},
    )).json()['data']
    operations = set(root_delegation['contract']['allowed_operations'])
    assert operations == {'context.read'}
    assert root_delegation['contract']['delegable_operations'] == [
        'script.document.draft', 'script.document.review']
    assert root_delegation['contract']['execution_authorization'] is None
    final_graph = read_canvas(ctx.state_dir, canvas_id)
    projected_scripts = [item for item in final_graph['nodes']
                         if item['type'] == 'textAnnotationNode'
                         and (item.get('data') or {}).get('formalScriptRef', {}).get('kind') == 'episode_script']
    assert len(projected_scripts) == 1
    assert projected_scripts[0]['data']['referenceOnly'] is True
    assert not any(item['type'] == 'scriptNode' for item in final_graph['nodes'])


async def test_formal_prepare_blocks_when_frozen_writer_lacks_capability_before_agent_run(
        api, ready, service, monkeypatch):
    from novelvideo.creation.builtin_agents import catalog
    from novelvideo.creation.collaboration import Collaboration
    from novelvideo.creation.delegations import Delegations

    client, _ = api
    profiles = ready[2]

    async def adopt_builtin(agent_id, request_id):
        item = next(value for value in catalog() if value['id'] == agent_id)
        return await profiles.apply(command('adopt', {
            'source': 'builtin', 'ref': item['source']['ref'], 'proof': item['proof'],
        }, request_id))

    lead = await adopt_builtin('builtin:creative-lead', 'formal-preflight-lead')
    writer = await adopt_builtin('builtin:text-writer', 'formal-preflight-writer')
    reviewer = await adopt_builtin('builtin:script-editor', 'formal-preflight-reviewer')
    await service.apply(command('main.select', {
        'expected_binding_revision': 1, 'adoption_id': lead['adoption_id'],
        'expected_adoption_revision': lead['revision'],
    }, 'formal-preflight-select-lead'))
    crew = Collaboration(service.ctx, username='alice', canvas_id=ready[0],
                         conversation_id=ready[1])
    crew.join(writer['adoption_id'], writer['revision'])
    crew.join(reviewer['adoption_id'], reviewer['revision'])

    async def intake(*args, **kwargs):
        return {'intent': 'prepare', 'preparation_kind': 'formal_episode_script',
                'message': '安排主笔起草并由编辑审阅正式剧本。',
                'acceptance': ['形成正式剧本草稿与独立审阅。']}

    agent_calls = []

    async def run_agent(*args, **kwargs):
        agent_calls.append(1)
        return await _decision()

    original_agent = Delegations._agent

    def old_writer_revision(self, conn, adoption_id, revision=None):
        adopted = original_agent(self, conn, adoption_id, revision)
        if adoption_id == writer['adoption_id'] and revision == writer['revision']:
            adopted['effective']['capabilities'] = [capability for capability in
                adopted['effective'].get('capabilities', []) if capability != 'script.document.draft']
        return adopted

    monkeypatch.setattr(discussions, 'run_message', intake)
    monkeypatch.setattr(agent_model, 'run_decision', run_agent)
    monkeypatch.setattr(Delegations, '_agent', old_writer_revision)
    response = await client.post('/projects/p/creation/discussions', json=_body(ready,
        message='安排主笔与编辑完成正式剧本。', selection={'kind': 'nodes', 'node_ids': ['a', 'b']}))

    assert response.status_code == 200, response.text
    task = response.json()['data']['collaboration_task']
    assert task['state'] == 'blocked'
    assert task['error_code'] == 'formal_script_specialists_missing'
    assert task['resume_blocked_reason'] == 'formal_script_specialists_missing'
    assert task['delegation_id'] is None and task['run_id'] is None
    assert agent_calls == []


@pytest.mark.parametrize(('state', 'phase', 'error_code', 'expected_status', 'expected_error'), [
    ('blocked', 'invalid_response', 'model_response_invalid', 200, None),
    ('blocked', 'invalid_response', 'formal_script_specialists_missing', 409,
     'formal_script_specialists_missing'),
    ('blocked', 'decision_saved', 'node_execution_failed', 200, None),
    ('submission_unknown', 'submission_unknown', 'model_submission_unknown', 409,
     'model_submission_unknown'),
])
async def test_task_resume_only_retries_known_invalid_response(
        api, ctx, ready, monkeypatch, state, phase, error_code, expected_status, expected_error):
    from novelvideo.creation.collaboration_tasks import CollaborationTasks

    client, _ = api
    round_state = {'phase': phase, 'attempt_id': 'attempt-1'}
    if error_code == 'formal_script_specialists_missing':
        round_state['response_received'] = True
    if error_code == 'node_execution_failed':
        round_state.update(actions_admitted=[0], actions_done=[], decision={
            'actions': [{'operation': 'node.run', 'payload': {
                'node_id': 'candidate-node', 'operation': 'generate', 'expected_revision': 7,
            }}],
        })
    run = {'id': 'run-resume-test', 'revision': 4, 'state': state,
           'error': {'code': error_code,
                     **({'response_received': True}
                        if error_code == 'formal_script_specialists_missing' else {})},
           'max_model_calls': 8,
           'rounds': [round_state],
           'children': [], 'outputs': []}
    row = {
        'id': 'task-resume-test', 'revision': 3, 'state': state, 'run_id': run['id'],
        'source_request_id': 'source-request', 'canvas_id': ready[0],
        'conversation_id': ready[1], 'user_message': '请准备候选内容。',
        'intent': 'prepare', 'speaker': {}, 'goal': '准备候选内容。', 'acceptance': [],
        'requested_action': None, 'execution_authorization': None, 'max_model_calls': 8,
        'scope_snapshot': {'scope': {'kind': 'nodes', 'canvas_id': ready[0]},
                           'node_ids': ['a'], 'observed_canvas_revision': 1},
        'delegation_id': 'delegation-resume-test', 'error_code': None,
        'created_at': '2026-10-18T00:00:00+00:00', 'updated_at': '2026-10-18T00:00:00+00:00',
    }

    async def resolve(_role):
        return ctx

    task = CollaborationTasks(ctx, username='alice', canvas_id=ready[0],
                              conversation_id=ready[1], resolve_context=resolve)
    task._get = lambda _identifier: row
    scheduled = []
    calls = []

    class Store:
        def continuations(self, *_args, **_kwargs):
            return []

        def update(self, _identifier, **changes):
            row.update(state=changes['state'])
            return row

    class Runs:
        async def get(self, _identifier):
            return dict(run)

        async def control(self, identifier, action, body):
            calls.append((identifier, action, body))
            run.update(state='queued', revision=run['revision'] + 1)
            run.pop('error', None)
            return {'run': dict(run)}

    fake_runs = Runs()

    async def schedule(_runs, run_id, _background):
        scheduled.append(run_id)

    async def runs_for(_ctx):
        return fake_runs

    async def no_candidates(*_args):
        return []

    task.store = Store()
    task._schedule_drive = schedule
    task._runs = runs_for
    task._prepared_execution_candidates = no_candidates

    async def factory(*_args, **_kwargs):
        return task

    monkeypatch.setattr(task_routes, 'collaboration_tasks_service', factory)
    response = await client.post(
        f"/projects/p/creation/collaboration-tasks/{row['id']}/resume",
        params={'canvas_id': ready[0], 'conversation_id': ready[1]},
        json={'request_id': 'resume-test', 'expected_revision': row['revision']},
    )

    assert response.status_code == expected_status, response.text
    if expected_error:
        assert response.json()['detail']['code'] == expected_error
        assert calls == [] and scheduled == []
    else:
        assert response.json()['data']['state'] == 'queued'
        assert response.json()['data']['resume_available'] is True
        assert response.json()['data']['resume_blocked_reason'] is None
        assert len(calls) == 1 and calls[0][1] == 'resume'
        assert scheduled == [run['id']]
    detail = await client.get(
        f"/projects/p/creation/collaboration-tasks/{row['id']}",
        params={'canvas_id': ready[0], 'conversation_id': ready[1]},
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()['data']['resume_available'] is (expected_error is None)
    assert detail.json()['data']['resume_blocked_reason'] == expected_error


def test_saved_node_run_failure_is_not_classified_as_model_submission_unknown():
    run = {'rounds': [{
        'phase': 'decision_saved',
        'actions_admitted': [0],
        'actions_done': [],
        'decision': {'actions': [{'operation': 'node.run', 'payload': {
            'node_id': 'candidate-node', 'operation': 'generate', 'expected_revision': 7,
        }}]},
    }]}

    error = run_module._drive_failure_error(ValueError('provider configuration is unavailable'), run)

    assert error == {
        'code': 'node_execution_failed',
        'message': '节点执行失败；恢复时只对账原节点请求，不会创建替代运行',
    }


def test_node_execution_failure_without_pending_admitted_run_is_not_resumable():
    from novelvideo.creation.collaboration_tasks import _resume_status

    available, reason = _resume_status({
        'state': 'blocked',
        'error': {'code': 'node_execution_failed'},
        'rounds': [{'phase': 'decision_saved', 'actions_admitted': [0], 'actions_done': [],
                    'decision': {'actions': [{'operation': 'asset.design.create'}]}}],
    })

    assert available is False
    assert reason == 'node_execution_failed'


def test_formal_script_preparation_does_not_inherit_asset_or_execution_operations():
    from novelvideo.creation.collaboration_tasks import CollaborationTasks

    row = {
        'preparation_kind': 'formal_episode_script',
        'speaker': {'profile': {'capabilities': [
            'proposal.create', 'context.read', 'media.propose', 'node.run',
        ]}},
    }

    assert CollaborationTasks._allowed_operations(row) == ['context.read']


def test_collaboration_task_store_persists_formal_preparation_kind(tmp_path):
    from novelvideo.creation.collaboration_tasks import CollaborationTaskStore

    store = CollaborationTaskStore(tmp_path / 'tasks.sqlite3')
    scope = {'scope': {'canvas_id': 'canvas'}, 'conversation_id': 'conversation'}
    created = store._ensure(
        identifier='task-formal-script', owner_id='alice', project_id='p',
        request_id='request-formal-script', request_hash='payload-hash',
        user_message='写正式剧本', intent='prepare', preparation_kind='formal_episode_script',
        speaker={'profile': {'capabilities': ['proposal.create']}}, specialists=[],
        scope_snapshot=scope, acceptance=['完成正式剧本初稿'], requested_action=None,
        state='bridging')

    replay = store._ensure(
        identifier='task-formal-script', owner_id='alice', project_id='p',
        request_id='request-formal-script', request_hash='payload-hash',
        user_message='写正式剧本', intent='prepare', preparation_kind='formal_episode_script',
        speaker={'profile': {'capabilities': ['proposal.create']}}, specialists=[],
        scope_snapshot=scope, acceptance=['完成正式剧本初稿'], requested_action=None,
        state='bridging')
    stored = store.get('task-formal-script', owner_id='alice', project_id='p',
                       canvas_id='canvas', conversation_id='conversation')

    assert created['preparation_kind'] == 'formal_episode_script'
    assert replay['id'] == created['id']
    assert stored['preparation_kind'] == 'formal_episode_script'


async def test_builtin_main_character_and_scene_roles_keep_prepare_capabilities(ready):
    from novelvideo.creation.builtin_agents import resolve
    from novelvideo.creation.collaboration_tasks import PREPARATION_OPERATIONS
    from .test_ontology import command

    profiles = ready[2]
    for identifier in ('builtin:creative-lead', 'builtin:character-designer', 'builtin:scene-designer'):
        fixed = resolve({'id': identifier, 'revision': 2 if identifier == 'builtin:creative-lead' else 1})
        adopted = await profiles.apply(command('adopt', {
            'source': 'builtin', 'ref': fixed['source']['ref'], 'proof': fixed['proof'],
        }, f'prepare-capability-{identifier}'))
        capabilities = set(adopted['effective']['capabilities'])
        assert PREPARATION_OPERATIONS.intersection(capabilities), identifier


async def test_prepare_asset_design_runs_real_tool_and_projects_node_candidate(
        api, ctx, ready, service, monkeypatch):
    from PIL import Image
    from novelvideo.chat.conversations import ConversationStore
    from novelvideo.creation.builtin_agents import catalog
    from novelvideo.creation.canvas_workspaces import CanvasCreate, CanvasWorkspace
    from novelvideo.creation.projection import ProjectionService
    from novelvideo.creation.store import ObjectStore
    from novelvideo.freezone.orchestration.adapters import NodeAdapter
    from novelvideo.freezone.canvas_store import canvas_path, read_canvas
    from .test_ontology import command

    client, _ = api
    profiles = ready[2]
    objects = ObjectStore(ctx.state_dir)
    created = CanvasWorkspace(objects, project_id='p').create(
        'alice', CanvasCreate(request_id='asset-design-concept', kind='concept', title='人物设定'))
    ProjectionService(objects).apply(created.operation_id)
    canvas_id = created.object_refs[0]['canvas_id']
    reference_path = ctx.output_dir / 'assets' / 'selected.png'
    reference_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (16, 16), color=(40, 90, 140)).save(reference_path)
    selected_image_url = '/static/projects/p/assets/selected.png'
    graph = read_canvas(ctx.state_dir, canvas_id)
    graph['nodes'].extend([
        {'id': 'asset-scope', 'type': 'groupNode', 'position': {'x': 800, 'y': 0},
         'data': {'label': '已选角色范围'}},
        {'id': 'selected-image', 'type': 'imageGenNode', 'parentId': 'asset-scope',
         'position': {'x': 20, 'y': 40},
         'data': {'imageUrl': selected_image_url, 'prompt': '已确认的角色参考图'}},
        {'id': 'unselected-image', 'type': 'imageGenNode', 'position': {'x': 1600, 'y': 0},
         'data': {'imageUrl': '/static/projects/p/assets/not-selected.png'}},
    ])
    graph['revision'] += 1
    canvas_path(ctx.state_dir, canvas_id).write_text(json.dumps(graph), encoding='utf-8')
    conversation = ConversationStore('alice', 'project', 'p').create(
        '人物设定讨论', canvas_id=canvas_id, request_id='asset-design-conversation')

    lead = next(item for item in catalog() if item['id'] == 'builtin:creative-lead')
    adopted = await profiles.apply(command('adopt', {
        'source': 'builtin', 'ref': lead['source']['ref'], 'proof': lead['proof'],
    }, 'asset-design-builtin-lead'))
    asset_artist = next(item for item in catalog() if item['id'] == 'builtin:art-asset-designer')
    adopted_asset_artist = await profiles.apply(command('adopt', {
        'source': 'builtin', 'ref': asset_artist['source']['ref'], 'proof': asset_artist['proof'],
    }, 'asset-design-builtin-artist'))
    await service.apply(command('main.select', {
        'expected_binding_revision': 1, 'adoption_id': adopted['adoption_id'],
        'expected_adoption_revision': adopted['revision'],
    }, 'asset-design-select-lead'))
    from novelvideo.creation.collaboration import Collaboration
    Collaboration(service.ctx, username='alice', canvas_id=canvas_id,
                  conversation_id=conversation['id']).join(
                      adopted_asset_artist['adoption_id'], adopted_asset_artist['revision'])

    async def intake(*args, **kwargs):
        return {'intent': 'prepare', 'message': '我会先建立人物设定草稿和可执行的立绘节点。',
                'acceptance': ['创建人物设计草稿并落下立绘运行候选。']}

    model_goals = []

    async def run_agent(_ctx, _route, prompt, **_identity):
        value = json.loads(prompt)
        model_goals.append(value['goal'])
        if value['allowed_operations'] == ['node.run']:
            node_id = value['node_execution']['authorization']['allowed_node_ids'][0]
            revision = value['node_execution']['targets'][node_id]['expected_revision']
            return await _decision({
                'operation': 'node.run',
                'payload': {'node_id': node_id, 'operation': 'generate',
                            'expected_revision': revision},
            })
        assert [item['ref'] for item in value['inputs']] == [{
            'kind': 'canvas_node', 'canvas_id': canvas_id, 'node_id': 'selected-image',
        }]
        asset_semantics = value['operation_semantics']['asset.design.create']
        assert 'visual reference' in asset_semantics
        assert 'draft_title' in asset_semantics and 'draft_text' in asset_semantics
        designs = [
            ('林岚', '林岚人物设定', '林岚是夜班记者，擅长从现场细节中发现矛盾。'),
            ('陈姨', '陈姨人物设定', '陈姨是社区门卫，习惯把访客记录保存在旧账本里。'),
            ('雨夜门厅', '雨夜门厅场景设定', '老楼门厅在暴雨停电后只剩窗外微光。'),
        ]
        return await _decision(*[{
            'operation': 'asset.design.create',
            'payload': {
                'target_node_ids': ['asset-scope'], 'input_indices': [0], 'acceptance_indices': [0],
                    'kind': 'scene' if '场景' in title else 'character',
                'name': name, 'description': f'{name}的可信资产描述',
                'prompt': f'{name}的写实影视设计。',
                'reference_indices': [0], 'draft_title': title, 'draft_text': draft,
            },
        } for name, title, draft in designs])

    provider_submissions = []

    async def submit_image(_adapter, run, attempt, _upstream):
        provider_submissions.append(attempt['node_id'])
        return {'substitute_provider_job': attempt['id']}

    async def reconcile_image(_adapter, _run, _attempt):
        return {'status': 'succeeded', 'result': {'imageUrl': '/media/substitute.png'},
                'evidence': {'provider': 'test-substitute'}}

    async def verify_image(_adapter, _run, _attempt):
        return [{'kind': 'image', 'sha256': 'a' * 64}]

    monkeypatch.setattr(NodeAdapter, 'submit', submit_image)
    monkeypatch.setattr(NodeAdapter, 'reconcile', reconcile_image)
    monkeypatch.setattr(NodeAdapter, 'verify', verify_image)

    monkeypatch.setattr(discussions, 'run_message', intake)
    monkeypatch.setattr(agent_model, 'run_decision', run_agent)
    ready_for_canvas = (canvas_id, conversation['id'], profiles, adopted)
    response = await client.post('/projects/p/creation/discussions', json=_body(
        ready_for_canvas, selection={'kind': 'nodes', 'node_ids': ['asset-scope', 'selected-image']}))

    assert response.status_code == 200, response.text
    task = response.json()['data']['collaboration_task']
    assert task['run_id'] and task['delegation_id']
    detail_response = await client.get(f"/projects/p/creation/collaboration-tasks/{task['id']}", params={
        'canvas_id': canvas_id, 'conversation_id': conversation['id'],
    })
    assert detail_response.status_code == 200, detail_response.text
    detail = detail_response.json()['data']
    assert detail['state'] == 'delivered', (
        detail.get('error_code'), detail.get('run', {}).get('state'),
        detail.get('run', {}).get('error'), detail.get('run', {}).get('unmet'),
        detail.get('run', {}).get('unmet_by_task'),
        [item.get('phase') for item in detail.get('run', {}).get('rounds', [])],
    )
    assert detail['execution_authorization'] is None
    assert len(detail['prepared_execution_candidates']) == 3
    prepared_candidates = detail['prepared_execution_candidates']
    assert all(item['status'] == 'ready_to_authorize' for item in prepared_candidates)
    candidates = [item['candidate'] for item in prepared_candidates]
    assert all(candidate['canvas_id'] == canvas_id for candidate in candidates)
    assert all(candidate['node_type'] == 'imageGenNode' for candidate in candidates)
    assert all(candidate['operation'] == 'generate' and candidate['max_node_runs'] == 1
               for candidate in candidates)

    run = detail['run']
    assert run['state'] == 'delivered'
    assert len(run['outputs']) == 3
    assert all(output['operation'] == 'asset.design.create' for output in run['outputs'])
    output_by_request = {output['prepared_execution']['request_id']: output
                         for output in run['outputs']}
    assert set(output_by_request) == {candidate['request_id'] for candidate in candidates}
    canvas = read_canvas(ctx.state_dir, canvas_id)
    source_texts = set()
    for candidate in candidates:
        output = output_by_request[candidate['request_id']]
        assert output['prepared_execution'] == candidate
        assert output['asset_design']['prepared_execution'] == candidate
        design = output['asset_design']
        assert design['projection_status'] == 'applied'
        assert {'source': 'selected-image', 'target': candidate['node_id']} in [
            {key: edge[key] for key in ('source', 'target')} for edge in canvas['edges']]
        image = next(node for node in canvas['nodes'] if node['id'] == candidate['node_id'])
        assert image['type'] == 'imageGenNode'
        assert image['data']['designBinding']['design_ref'] == design['design_ref']
        asset_design = ObjectStore(ctx.state_dir).read(design['design_ref']['id'])
        assert asset_design['payload']['source_proposal_origin']['kind'] == 'operation_created'
        source = ObjectStore(ctx.state_dir).read(design['source_proposal_ref']['id'])
        assert source['kind'] == 'story_fragment'
        source_texts.add(source['payload']['text'])
    assert source_texts == {
        '林岚是夜班记者，擅长从现场细节中发现矛盾。',
        '陈姨是社区门卫，习惯把访客记录保存在旧账本里。',
        '老楼门厅在暴雨停电后只剩窗外微光。',
    }

    continuation_run = None
    first_revision = detail['revision']
    for index, candidate in enumerate(candidates):
        authorized = await client.post(
            f"/projects/p/creation/collaboration-tasks/{task['id']}/authorize-candidate",
            params={'canvas_id': canvas_id, 'conversation_id': conversation['id']},
            json={'request_id': f'authorize-image-candidate-{index}',
                  'expected_revision': first_revision,
                  'source_operation_id': candidate['source_operation_id'],
                  'source_request_id': candidate['request_id'], 'confirmed': True},
        )
        assert authorized.status_code == 200, authorized.text
        continued_response = await client.get(
            f"/projects/p/creation/collaboration-tasks/{task['id']}", params={
                'canvas_id': canvas_id, 'conversation_id': conversation['id'],
            })
        assert continued_response.status_code == 200, continued_response.text
        continued = continued_response.json()['data']
        continuation = next(item for item in continued['continuations']
                            if item['candidate']['request_id'] == candidate['request_id'])
        assert continuation['state'] == 'delivered', (
            continuation.get('error_code'), continuation.get('run', {}).get('error'),
            continuation.get('run', {}).get('unmet'),
        )
        continuation_delegation = (await client.get(
            f"/projects/p/creation/delegations/{continuation['delegation_id']}", params={
                'canvas_id': canvas_id, 'conversation_id': conversation['id'],
            })).json()['data']
        assert [item['ref'] for item in continuation_delegation['contract']['inputs']] == [{
            'kind': 'canvas_node', 'canvas_id': canvas_id, 'node_id': 'selected-image',
        }]
        assert continuation_delegation['contract']['execution_authorization']['allowed_node_ids'] == [
            candidate['node_id']]
        continuation_run = continuation['run'] if index == 0 else continuation_run
        assert continuation['run']['state'] == 'delivered'
        assert continuation['run']['outputs'][0]['operation'] == 'node.run'
        assert continuation['run']['outputs'][0]['node_id'] == candidate['node_id']
        assert provider_submissions == [item['node_id'] for item in candidates[:index + 1]]
        current_canvas = read_canvas(ctx.state_dir, canvas_id)
        generated = next(node for node in current_canvas['nodes'] if node['id'] == candidate['node_id'])
        assert generated['data']['imageUrl'] == '/media/substitute.png'
        first_revision = continued['revision']

    first_candidate = candidates[0]
    replay = await client.post(
        f"/projects/p/creation/collaboration-tasks/{task['id']}/authorize-candidate",
        params={'canvas_id': canvas_id, 'conversation_id': conversation['id']},
        json={'request_id': 'authorize-image-candidate-0',
              'expected_revision': detail['revision'],
              'source_operation_id': first_candidate['source_operation_id'],
              'source_request_id': first_candidate['request_id'], 'confirmed': True},
    )
    assert replay.status_code == 200, replay.text
    assert any(item['run_id'] == continuation_run['id'] for item in replay.json()['data']['continuations'])
    assert provider_submissions == [item['node_id'] for item in candidates]

    continued_response = await client.get(
        f"/projects/p/creation/collaboration-tasks/{task['id']}", params={
            'canvas_id': canvas_id, 'conversation_id': conversation['id'],
        })
    continued = continued_response.json()['data']
    duplicate_grant = await client.post(
        f"/projects/p/creation/collaboration-tasks/{task['id']}/authorize-candidate",
        params={'canvas_id': canvas_id, 'conversation_id': conversation['id']},
        json={'request_id': 'authorize-image-candidate-again',
              'expected_revision': continued['revision'],
              'source_operation_id': first_candidate['source_operation_id'],
              'source_request_id': first_candidate['request_id'], 'confirmed': True},
    )
    assert duplicate_grant.status_code == 409
    assert duplicate_grant.json()['detail']['code'] == 'candidate_already_authorized'
    assert provider_submissions == [item['node_id'] for item in candidates]
    assert len(model_goals) == 4

async def test_execute_request_waits_for_exact_user_authorization(api, ready, service, monkeypatch):
    from novelvideo.creation.builtin_agents import catalog
    from .test_ontology import command

    client, _ = api
    lead = next(item for item in catalog() if item['id'] == 'builtin:creative-lead')
    profiles = ready[2]
    adopted = await profiles.apply(command('adopt', {
        'source': 'builtin', 'ref': lead['source']['ref'], 'proof': lead['proof'],
    }, 'authorization-builtin-lead'))
    await service.apply(command('main.select', {
        'expected_binding_revision': 1, 'adoption_id': adopted['adoption_id'],
        'expected_adoption_revision': adopted['revision'],
    }, 'authorization-select-lead'))
    from .test_delegations import edit_graph
    edit_graph(profiles.ctx, ready[0], lambda graph: graph['nodes'].append({
        'id': 'image', 'type': 'imageGenNode', 'data': {'prompt': '旧提示词'},
    }))

    async def intake(*args, **kwargs):
        return {
            'intent': 'execute_request', 'message': '建议编辑节点 image 的提示词。',
            'requested_action': {'operation': 'node.patch', 'node_id': 'image',
                                 'effect': '为开场添加一个悬念候选'},
        }

    monkeypatch.setattr(discussions, 'run_message', intake)
    monkeypatch.setattr(agent_model, 'run_decision', lambda *a, **k: _decision())

    response = await client.post('/projects/p/creation/discussions', json=_body(
        ready, selection={'kind': 'nodes', 'node_ids': ['image']}))

    assert response.status_code == 200, response.text
    task = response.json()['data']['collaboration_task']
    assert task['state'] == 'awaiting_authorization'
    assert task['requested_action'] == {
        'operation': 'node.patch', 'node_id': 'image',
        'effect': '为开场添加一个悬念候选',
    }
    assert task['run_id'] is None and task['delegation_id'] is None

    unconfirmed = await client.post(
        f"/projects/p/creation/collaboration-tasks/{task['id']}/authorize",
        params={'canvas_id': ready[0], 'conversation_id': ready[1]},
        json={'request_id': 'confirm-execute-1', 'expected_revision': task['revision'],
              'confirmed': False},
    )
    assert unconfirmed.status_code == 409
    assert unconfirmed.json()['detail']['code'] == 'authorization_required'
    current = await client.get(f"/projects/p/creation/collaboration-tasks/{task['id']}", params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    })
    assert current.json()['data']['state'] == 'awaiting_authorization'
    assert current.json()['data']['run_id'] is None

    confirmed = await client.post(
        f"/projects/p/creation/collaboration-tasks/{task['id']}/authorize",
        params={'canvas_id': ready[0], 'conversation_id': ready[1]},
        json={'request_id': 'confirm-execute-1', 'expected_revision': task['revision'],
              'confirmed': True},
    )
    assert confirmed.status_code == 200, confirmed.text
    authorized_task = confirmed.json()['data']
    assert authorized_task['source_request_id'] == 'chat-task-1'
    assert authorized_task['state'] == 'queued'
    assert authorized_task['run_id'] and authorized_task['delegation_id']
    completed = await client.get(f"/projects/p/creation/collaboration-tasks/{task['id']}", params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    })
    assert completed.json()['data']['state'] == 'partial'
    delegation = await client.get(f"/projects/p/creation/delegations/{authorized_task['delegation_id']}", params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    })
    assert delegation.status_code == 200, delegation.text
    assert delegation.json()['data']['contract']['execution_authorization'] == {
        'allowed_node_ids': ['image'],
        'allowed_node_operations': [{'node_id': 'image', 'operation': 'patch'}],
        'max_node_runs': 0,
    }
    replay = await client.post(
        f"/projects/p/creation/collaboration-tasks/{task['id']}/authorize",
        params={'canvas_id': ready[0], 'conversation_id': ready[1]},
        json={'request_id': 'confirm-execute-1', 'expected_revision': task['revision'],
              'confirmed': True},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()['data']['run_id'] == authorized_task['run_id']
    assert replay.json()['data']['delegation_id'] == authorized_task['delegation_id']


async def test_intake_stores_stale_scope_and_does_not_bridge_updated_nodes(api, ready, monkeypatch):
    from .test_delegations import edit_graph

    client, _ = api

    async def intake(*args, **kwargs):
        edit_graph(ready[2].ctx, ready[0], lambda graph: graph['nodes'][1]['data'].update(text='并发修改'))
        return {'intent': 'prepare', 'message': '我会整理两个方向。',
                'acceptance': ['给出两个方向。']}

    monkeypatch.setattr(discussions, 'run_message', intake)
    monkeypatch.setattr(agent_model, 'run_decision', lambda *a, **k: pytest.fail('stale intake must not start a run'))

    response = await client.post('/projects/p/creation/discussions', json=_body(ready))

    assert response.status_code == 200, response.text
    task = response.json()['data']['collaboration_task']
    assert task['state'] == 'stale'
    assert task['run_id'] is None and task['delegation_id'] is None
    assert task['source_request_id'] == 'chat-task-1'
