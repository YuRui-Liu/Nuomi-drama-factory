import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from novelvideo.creation import discussions
from novelvideo.creation.operations import OperationError
from novelvideo.freezone.canvas_store import canvas_path
from .test_delegations import ctx, ready, service
from .test_ontology import command


def _request(ready, **changes):
    canvas_id, conversation_id = ready[:2]
    return {
        'request_id': 'discussion-1',
        'canvas_id': canvas_id,
        'conversation_id': conversation_id,
        'message': '  帮我梳理这个画布的故事方向。  ',
        **changes,
    }


@pytest.fixture
def discussion_service(ctx, ready, monkeypatch):
    async def resolve_context(role):
        assert role == 'viewer'
        return ctx

    monkeypatch.setattr(discussions, 'availability', lambda _: {
        'available': True,
        'code': 'configured_not_verified',
        'route': {'runtime': 'model_api', 'task_role': discussions.ROLE, 'model': 'test-model'},
        'configuration_fingerprint': 'config-1',
    })
    return discussions.Discussions(ctx, username='alice', canvas_id=ready[0],
                                   conversation_id=ready[1], resolve_context=resolve_context)


async def test_discussion_succeeds_without_node_selection(discussion_service, ready, monkeypatch):
    calls = []

    async def model(ctx, route, prompt, *, request_id):
        calls.append((route, prompt, request_id))
        return {'intent': 'reply', 'message': '可以从人物关系和场景目标两方面展开。'}

    monkeypatch.setattr(discussions, 'run_message', model)
    result = await discussion_service.submit(_request(ready))

    assert result['status'] == 'completed'
    assert result['message'] == '可以从人物关系和场景目标两方面展开。'
    assert calls[0][2] == 'discussion-1'
    assert '开场 A' in calls[0][1]
    assert 'node_ids' not in calls[0][1]


async def test_discussion_binds_project_main_and_records_actual_speaker(discussion_service, ready, monkeypatch):
    calls = []

    async def model(ctx, route, prompt, *, request_id):
        calls.append(json.loads(prompt))
        return {'intent': 'reply', 'message': '按主创档案继续讨论。'}

    monkeypatch.setattr(discussions, 'run_message', model)
    result = await discussion_service.submit(_request(ready))
    history = await discussion_service.history()

    speaker = result['speaker']
    assert speaker['kind'] == 'main'
    assert speaker['adoption_id'] == ready[3]['adoption_id']
    assert speaker['adoption_revision'] == 1
    assert speaker['profile']['name'] == '主创'
    assert calls[0]['current_speaker'] == speaker
    assert history['messages'][-1]['speaker'] == speaker
    assert history['messages'][-2]['speaker'] is None
    assert not history['messages'][-2]['speaker_recorded']
    assert history['messages'][-1]['speaker_recorded']


async def test_default_discussion_is_read_only_and_never_bridges_a_prepare_intent(
        discussion_service, ready, service, ctx, monkeypatch):
    await service.apply(command('main.clear', {'expected_binding_revision': 1}, 'clear-for-default-discussion'))
    from novelvideo.creation.collaboration import Collaboration
    collaboration = Collaboration(ctx, username='alice', canvas_id=ready[0], conversation_id=ready[1])
    assert collaboration.main() is None
    with pytest.raises(OperationError) as missing:
        collaboration.resolve_speaker()
    assert missing.value.code == 'missing_main'

    async def model(ctx, route, prompt, *, request_id):
        return {'intent': 'prepare', 'message': '我会为你创建任务。',
                'acceptance': ['形成正式草稿']}

    monkeypatch.setattr(discussions, 'run_message', model)
    monkeypatch.setattr(discussion_service, '_task_service',
                        lambda *_args, **_kwargs: pytest.fail('default assistant must not bridge a task'))
    result = await discussion_service.submit(_request(ready, request_id='default-prepare'))

    assert result['status'] == 'completed'
    assert result['speaker']['kind'] == 'default_discussion'
    assert result['speaker']['adoption_id'] is None
    assert result['speaker']['adoption_revision'] is None
    assert result['message'] == '当前使用默认创作助手进行只读讨论；本次未创建任务。选择项目主创后可启动创作任务。'
    assert 'collaboration_task' not in result
    assert discussion_service._history_messages()[-1]['speaker']['kind'] == 'default_discussion'


async def test_intake_prompt_uses_frozen_selection_snapshot(discussion_service, ctx, ready):
    path = canvas_path(ctx.state_dir, ready[0])
    original = json.loads(path.read_text(encoding='utf-8'))
    snapshot = discussion_service._selection_snapshot(
        ctx, ready[0], ready[1], {'kind': 'nodes', 'node_ids': ['a']})
    changed = json.loads(json.dumps(original))
    changed['nodes'][1]['data']['text'] = '源画布的新版本内容'
    path.write_text(json.dumps(changed), encoding='utf-8')

    prompt = discussion_service._prompt('继续讨论', {
        'kind': 'main', 'binding_revision': 1, 'adoption_id': ready[3]['adoption_id'],
        'adoption_revision': 1, 'profile': {'name': '主创'},
    }, snapshot)

    assert '开场 A' in prompt
    assert '源画布的新版本内容' not in prompt


async def test_intake_selection_context_identifies_image_scope_without_media_content(
        discussion_service, ctx, ready):
    path = canvas_path(ctx.state_dir, ready[0])
    graph = json.loads(path.read_text(encoding='utf-8'))
    graph['nodes'][1]['type'] = 'imageGenNode'
    private_url = 'https://private.example/images/selection.png'
    private_image_content = 'private-image-bytes-not-for-intake'
    graph['nodes'][1]['data'] = {
        'imageUrl': private_url,
        'imageBytes': private_image_content,
    }
    path.write_text(json.dumps(graph), encoding='utf-8')
    snapshot = discussion_service._selection_snapshot(
        ctx, ready[0], ready[1], {'kind': 'nodes', 'node_ids': ['a']})

    prompt = discussion_service._prompt('请基于已选图片准备设计。', {
        'kind': 'main', 'binding_revision': 1, 'adoption_id': ready[3]['adoption_id'],
        'adoption_revision': 1, 'profile': {'name': '主创'},
    }, snapshot)
    payload = json.loads(prompt)
    selection = payload['selection_context']
    system = discussions._system_prompt_for_prompt(prompt)

    assert selection['scope_kind'] == 'nodes'
    assert selection['scoped_image_nodes'] == [{
        'node_id': 'a', 'node_type': 'imageGenNode', 'image_result_fields': ['imageUrl'],
    }]
    assert private_url not in prompt
    assert private_image_content not in prompt
    assert '查看或判断图像像素' in system
    assert 'input_indices 由服务端桥接分配' in system
    assert '不要要求用户重复提供节点 ID' in system
    assert '不要要求用户提供 input_indices' in system


async def test_discussion_falls_back_but_task_speaker_resolution_stays_strict(
        discussion_service, ready, monkeypatch):
    from novelvideo.creation.delegations import Delegations
    from novelvideo.creation.collaboration import Collaboration
    from .test_ontology import command
    service = Delegations(discussion_service.ctx, username='alice', authority='user')
    await service.apply(command('main.clear', {'expected_binding_revision': 1}, 'clear-main-for-discussion'))
    called = []
    async def model(*args, **kwargs):
        called.append(True)
        return {'intent': 'reply', 'message': '可以先从人物关系讨论。'}

    monkeypatch.setattr(discussions, 'run_message', model)
    result = await discussion_service.submit(_request(ready))

    assert result['status'] == 'completed'
    assert result['speaker']['kind'] == 'default_discussion'
    assert called == [True]
    with pytest.raises(OperationError) as failure:
        Collaboration(discussion_service.ctx, username='alice', canvas_id=ready[0],
                      conversation_id=ready[1]).resolve_speaker()
    assert failure.value.code == 'missing_main'


async def test_explicit_target_uses_only_a_current_group_member(discussion_service, ready, monkeypatch):
    from novelvideo.creation.collaboration import Collaboration

    calls = []

    async def model(ctx, route, prompt, *, request_id):
        calls.append(json.loads(prompt)['current_speaker'])
        return {'intent': 'reply', 'message': '点名的专业建议。'}

    monkeypatch.setattr(discussions, 'run_message', model)
    collaboration = Collaboration(discussion_service.ctx, username='alice',
                                  canvas_id=ready[0], conversation_id=ready[1])
    collaboration.join(ready[3]['adoption_id'], 1)
    result = await discussion_service.submit(_request(ready, request_id='named-1',
                                                       target_adoption_id=ready[3]['adoption_id']))

    assert result['speaker']['kind'] == 'member'
    assert result['speaker']['adoption_id'] == ready[3]['adoption_id']
    assert calls == [result['speaker']]


async def test_explicit_target_can_reply_without_a_project_main(discussion_service, ready, monkeypatch):
    from novelvideo.creation.collaboration import Collaboration
    from novelvideo.creation.delegations import Delegations
    from .test_ontology import command

    await Delegations(discussion_service.ctx, username='alice', authority='user').apply(
        command('main.clear', {'expected_binding_revision': 1}, 'clear-main-for-targeted-discussion'))
    Collaboration(discussion_service.ctx, username='alice', canvas_id=ready[0],
                  conversation_id=ready[1]).join(ready[3]['adoption_id'], 1)
    calls = []

    async def model(ctx, route, prompt, *, request_id):
        calls.append(json.loads(prompt)['current_speaker'])
        return {'intent': 'reply', 'message': '点名成员仍可讨论。'}

    monkeypatch.setattr(discussions, 'run_message', model)
    result = await discussion_service.submit(_request(
        ready, request_id='member-without-main', target_adoption_id=ready[3]['adoption_id']))

    assert result['speaker']['kind'] == 'member'
    assert calls == [result['speaker']]


async def test_named_nonmember_is_rejected_without_a_model_call(discussion_service, ready, monkeypatch):
    calls = []

    async def model(*args, **kwargs):
        calls.append(True)
        return {'intent': 'reply', 'message': '不应调用'}

    monkeypatch.setattr(discussions, 'run_message', model)
    with pytest.raises(OperationError) as failure:
        await discussion_service.submit(_request(ready, target_adoption_id='not-a-member'))

    assert failure.value.code == 'not_group_member'
    assert calls == []


async def test_replay_keeps_frozen_speaker_route_and_profile_after_configuration_changes(
        discussion_service, ready, monkeypatch):
    calls = []

    async def model(ctx, route, prompt, *, request_id):
        calls.append((route, json.loads(prompt)['current_speaker']))
        return {'intent': 'reply', 'message': '固定回执。'}

    monkeypatch.setattr(discussions, 'run_message', model)
    request = _request(ready)
    first = await discussion_service.submit(request)

    from .test_ontology import command
    profile_changes = {'methods': '回执之后更新的方法'}
    profile_preview = ready[2].override_preview(ready[3]['adoption_id'], profile_changes)
    await ready[2].apply(command('override', {
        'adoption_id': ready[3]['adoption_id'], 'expected_revision': 1,
        'changes': profile_changes, 'proof': profile_preview['proof'],
    }, 'update-profile-after-first-message'))
    monkeypatch.setattr(discussions, 'availability', lambda _: pytest.fail('replay must use its stored route'))
    replay = await discussion_service.submit(request)
    stored = discussion_service._read_existing(discussions.DiscussionRequest.model_validate(request))

    assert replay == first
    assert replay['speaker']['adoption_id'] == ready[3]['adoption_id']
    assert replay['speaker']['profile']['methods'] == '先核对事实'
    assert stored['adoption_revision'] == 1
    assert json.loads(stored['model_route'])['model'] == 'test-model'
    assert calls == [(calls[0][0], first['speaker'])]


async def test_target_is_part_of_idempotency_payload(discussion_service, ready, monkeypatch):
    from novelvideo.creation.collaboration import Collaboration

    monkeypatch.setattr(discussions, 'run_message', lambda *args, **kwargs: {'message': '回执。'})
    Collaboration(discussion_service.ctx, username='alice', canvas_id=ready[0],
                  conversation_id=ready[1]).join(ready[3]['adoption_id'], 1)
    request = _request(ready, request_id='target-conflict',
                       target_adoption_id=ready[3]['adoption_id'])
    await discussion_service.submit(request)

    with pytest.raises(OperationError) as failure:
        await discussion_service.submit(_request(ready, request_id='target-conflict'))
    assert failure.value.code == 'idempotency_conflict'


async def test_same_request_replays_once_and_changed_payload_conflicts(discussion_service, ready, monkeypatch):
    calls = []

    async def model(*args, **kwargs):
        calls.append(1)
        return {'intent': 'reply', 'message': '已收到。'}

    monkeypatch.setattr(discussions, 'run_message', model)
    request = _request(ready)
    first = await discussion_service.submit(request)
    assert await discussion_service.submit(request) == first
    with pytest.raises(OperationError) as failure:
        await discussion_service.submit(_request(ready, message='不同内容'))

    assert failure.value.code == 'idempotency_conflict'
    assert calls == [1]


@pytest.mark.parametrize(('status', 'reply'), [
    ('completed', '升级前已经生成的回复。'),
    ('submission_unknown', None),
])
async def test_legacy_request_hash_replays_without_resubmitting(
        discussion_service, ready, monkeypatch, status, reply):
    request = discussions.DiscussionRequest.model_validate(_request(ready, request_id=f'legacy-{status}'))
    legacy_payload = json.dumps({
        'canvas_id': request.canvas_id,
        'conversation_id': request.conversation_id,
        'message': request.message,
    }, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    legacy_hash = hashlib.sha256(legacy_payload.encode('utf-8')).hexdigest()
    conn = discussion_service._connect()
    try:
        conn.execute('''CREATE TABLE creation_discussions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            canvas_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','completed','submission_unknown')),
            reply TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(owner_id, project_id, canvas_id, conversation_id, request_id)
        )''')
        conn.execute('''INSERT INTO creation_discussions
            (owner_id, project_id, canvas_id, conversation_id, request_id, payload_hash,
             message, status, reply, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '2026-10-17T00:00:00Z', '2026-10-17T00:00:00Z')''',
            (discussion_service.owner_id, discussion_service.project_id, request.canvas_id,
             request.conversation_id, request.request_id, legacy_hash, request.message, status, reply))
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(discussions, 'run_message', lambda *args, **kwargs: pytest.fail('legacy replay must not resubmit'))
    monkeypatch.setattr(discussions, 'availability', lambda _: pytest.fail('legacy replay must use its stored receipt'))

    receipt = await discussion_service.submit(request)

    assert receipt['status'] == status
    assert receipt.get('message') == reply
    assert receipt['speaker'] is None
    assert receipt['speaker_recorded'] is False
    history = discussion_service._history_messages()
    assert all(item['speaker'] is None and not item['speaker_recorded'] for item in history)


async def test_timeout_is_durable_unknown_and_never_resubmitted(discussion_service, ready, monkeypatch):
    calls = []

    async def model(*args, **kwargs):
        calls.append(1)
        raise TimeoutError('private provider details')

    monkeypatch.setattr(discussions, 'run_message', model)
    request = _request(ready)
    first = await discussion_service.submit(request)
    second = await discussion_service.submit(request)
    history = await discussion_service.history()

    assert first['status'] == second['status'] == 'submission_unknown'
    assert calls == [1]
    assert history['messages'][-1]['status'] == 'submission_unknown'
    assert 'private provider' not in json.dumps(history)
    assert first['diagnostic'] == {
        'outcome': 'submission_unknown',
        'code': 'model_submission_unknown',
        'type': 'TimeoutError',
        'http_status': None,
        'response_received': False,
    }
    assert history['messages'][-1]['diagnostic'] == first['diagnostic']


async def test_unstaged_value_error_remains_unknown_and_never_resubmitted(
        discussion_service, ready, monkeypatch):
    calls = []

    async def model(*args, **kwargs):
        calls.append(1)
        raise ValueError('API key=private initialization details')

    monkeypatch.setattr(discussions, 'run_message', model)
    request = _request(ready)
    first = await discussion_service.submit(request)
    second = await discussion_service.submit(request)
    history = await discussion_service.history()

    assert first['status'] == second['status'] == 'submission_unknown'
    assert first['diagnostic'] == {
        'outcome': 'submission_unknown',
        'code': 'model_submission_unknown',
        'type': 'ValueError',
        'http_status': None,
        'response_received': False,
    }
    assert calls == [1]
    assert 'private initialization details' not in json.dumps(history)
    assert history['messages'][-1]['diagnostic'] == first['diagnostic']


async def test_explicit_provider_http_error_is_distinguished_without_body(
        discussion_service, ready, monkeypatch):
    class ProviderHTTPError(Exception):
        status_code = 429

    calls = []

    async def model(*args, **kwargs):
        calls.append(1)
        raise ProviderHTTPError('Authorization: secret provider response body')

    monkeypatch.setattr(discussions, 'run_message', model)
    request = _request(ready)
    first = await discussion_service.submit(request)
    second = await discussion_service.submit(request)
    history = await discussion_service.history()

    assert first['status'] == second['status'] == 'submission_unknown'
    assert first['diagnostic'] == {
        'outcome': 'provider_rejected',
        'code': 'provider_http_error',
        'type': 'ProviderHTTPError',
        'http_status': 429,
        'response_received': False,
    }
    assert calls == [1]
    assert 'secret provider response body' not in json.dumps(history)
    assert history['messages'][-1]['diagnostic'] == first['diagnostic']


async def test_invalid_model_reply_is_distinguished_from_unknown_submission(
        discussion_service, ready, monkeypatch):
    async def model(*args, **kwargs):
        return {'intent': 'reply', 'message': ''}

    monkeypatch.setattr(discussions, 'run_message', model)
    result = await discussion_service.submit(_request(ready))

    assert result['status'] == 'submission_unknown'
    assert result['diagnostic']['outcome'] == 'response_invalid'
    assert result['diagnostic']['code'] == 'model_response_invalid'
    assert result['diagnostic']['type'] == 'ValidationError'
    assert result['diagnostic']['response_received'] is True


def test_existing_discussion_store_gains_diagnostic_columns(discussion_service):
    conn = discussion_service._connect()
    try:
        conn.execute('''CREATE TABLE creation_discussions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            canvas_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','completed','submission_unknown')),
            reply TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(owner_id, project_id, canvas_id, conversation_id, request_id)
        )''')
        conn.commit()
        discussion_service._create_table(conn)
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(creation_discussions)')}
    finally:
        conn.close()

    assert {'error_code', 'error_type', 'http_status', 'response_received',
            'speaker_kind', 'binding_revision', 'adoption_id', 'adoption_revision',
            'effective_profile', 'model_route'} <= columns


async def test_cross_user_and_cross_canvas_conversations_are_denied(discussion_service, ctx, ready):
    from novelvideo.creation.discussions import Discussions

    bob_ctx = SimpleNamespace(**vars(ctx))
    bob_ctx.requester_user_id = 'bob'

    async def resolve_bob(role):
        return bob_ctx

    other_user = Discussions(bob_ctx, username='bob', canvas_id=ready[0],
                             conversation_id=ready[1], resolve_context=resolve_bob)
    async def resolve_alice(role):
        return ctx

    other_canvas = Discussions(ctx, username='alice', canvas_id='different-canvas',
                               conversation_id=ready[1], resolve_context=resolve_alice)
    with pytest.raises(OperationError) as user_failure:
        await other_user.history()
    with pytest.raises(OperationError) as canvas_failure:
        await other_canvas.history()
    assert user_failure.value.code == canvas_failure.value.code == 'scope_violation'


async def test_access_revoked_after_model_call_discards_reply(discussion_service, ctx, ready, monkeypatch):
    calls = 0

    async def resolve_context(role):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise HTTPException(403, 'revoked')
        return ctx

    discussion_service.resolve_context = resolve_context
    model_calls = []

    async def model(*args, **kwargs):
        model_calls.append(1)
        return {'intent': 'reply', 'message': '不得向已撤销访问者返回的内容。'}

    monkeypatch.setattr(discussions, 'run_message', model)
    with pytest.raises(OperationError) as failure:
        await discussion_service.submit(_request(ready))

    db_path = ctx.state_dir / 'creation' / 'objects.sqlite3'
    with sqlite3.connect(db_path) as db:
        row = db.execute('SELECT status, reply FROM creation_discussions WHERE request_id=?',
                         ('discussion-1',)).fetchone()
    assert failure.value.code == 'scope_violation'
    assert row == ('submission_unknown', None)
    assert model_calls == [1]


async def test_discussion_prompt_redacts_credentials_and_media_locators(discussion_service, ctx, ready, monkeypatch):
    path = canvas_path(ctx.state_dir, ready[0])
    canvas = json.loads(path.read_text(encoding='utf-8'))
    canvas['nodes'][1]['data']['text'] = (
        'API key=secret-value; media https://media.invalid/private.png; local C:\\private\\frame.png'
    )
    path.write_text(json.dumps(canvas), encoding='utf-8')
    prompts = []

    async def model(ctx, route, prompt, *, request_id):
        prompts.append(prompt)
        return {'intent': 'reply', 'message': '已收到。'}

    monkeypatch.setattr(discussions, 'run_message', model)
    await discussion_service.submit(_request(ready))

    assert 'secret-value' not in prompts[0]
    assert 'https://media.invalid/private.png' not in prompts[0]
    assert 'C:\\private\\frame.png' not in prompts[0]
    assert '[credential omitted]' in prompts[0]
    assert '[media locator omitted]' in prompts[0]


async def test_concurrent_duplicate_observes_pending_without_second_model_call(
        discussion_service, ready, monkeypatch):
    import asyncio

    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def model(*args, **kwargs):
        calls.append(1)
        entered.set()
        await release.wait()
        return {'intent': 'reply', 'message': '完成。'}

    monkeypatch.setattr(discussions, 'run_message', model)
    request = _request(ready)
    first = asyncio.create_task(discussion_service.submit(request))
    await entered.wait()
    concurrent = await discussion_service.submit(request)
    release.set()
    completed = await first

    assert concurrent['status'] == 'pending'
    assert completed['status'] == 'completed'
    assert calls == [1]


def test_reply_contract_only_accepts_message():
    reply = discussions.DiscussionReply.model_validate({'message': '纯文本讨论回复'})
    assert reply.message == '纯文本讨论回复'
    with pytest.raises(Exception):
        discussions.DiscussionReply.model_validate({'message': '建议执行', 'operation': 'node.run'})


def test_model_system_prompt_uses_frozen_professional_identity_and_stays_tool_free():
    prompt = json.dumps({'current_speaker': {
        'kind': 'member', 'adoption_id': 'adoption-1', 'adoption_revision': 3,
        'profile': {
            'name': '灯光师', 'responsibilities': '规划有动机的光源和色温',
            'methods': '从场景时段与环境光源开始分析', 'capabilities': ['proposal.create'],
        },
    }}, ensure_ascii=False)

    system = discussions._system_prompt_for_prompt(prompt)

    assert '灯光师' in system
    assert '规划有动机的光源和色温' in system
    assert 'proposal.create' in system
    assert '不授予任何工具、执行或项目写入权限' in system


def test_formal_episode_script_intake_uses_prepare_without_an_existing_script_node():
    prompt = json.dumps({
        'canvas_text': '当前画布没有 scriptNode，只有普通分组和文本节点。',
        'current_user_message': '请保存正式第1集 episode_script 草案，并由独立剧本编辑审阅；不做资产。',
        'current_speaker': {
            'profile': {
                'name': '主创制片统筹',
                'capabilities': ['proposal.create', 'node.run'],
                'required_node_operations': [
                    {'node_type': 'scriptNode', 'operation': 'plan_episode'},
                ],
            },
        },
    }, ensure_ascii=False)

    system = discussions._system_prompt_for_prompt(prompt)

    assert '正式 episode_script 草案' in system
    assert 'prepare' in system
    assert 'preparation_kind 为 formal_episode_script' in system
    assert '不要求画布中预先存在 scriptNode' in system
    assert '不得根据该字段虚构节点 ID' in system
    assert 'execute_request' in system


def test_formal_episode_script_kind_only_classifies_prepare_intake():
    intake = discussions.DiscussionIntake.model_validate({
        'intent': 'prepare', 'message': '安排主笔草拟并由编辑审阅正式剧本。',
        'acceptance': ['正式剧本草案与独立审阅报告'],
        'preparation_kind': 'formal_episode_script',
    })

    assert intake.preparation_kind == 'formal_episode_script'
    with pytest.raises(Exception, match='preparation kind'):
        discussions.DiscussionIntake.model_validate({
            'intent': 'execute_request', 'message': '执行节点操作。',
            'requested_action': {
                'operation': 'node.run', 'node_id': 'node-1',
                'node_operation': 'generate', 'effect': '生成图片。',
            },
            'preparation_kind': 'formal_episode_script',
        })


async def test_model_runtime_is_single_request_tool_free_and_billed_as_discussion(ctx, monkeypatch):
    from novelvideo import llm_instrumentation, ports
    from novelvideo.text_task_runtime.models import AgentTaskRouteSnapshot
    from novelvideo.text_task_runtime import runtime as runtime_module

    events = []

    class Meter:
        def set_llm_usage_context(self, *args, **kwargs):
            events.append(('set', args, kwargs))

        def clear_llm_usage_context(self):
            events.append(('clear',))

    class Runtime:
        def __init__(self, route, **kwargs):
            events.append(('runtime', route, kwargs))

        async def run_structured(self, **kwargs):
            events.append(('run', kwargs))
            return discussions.DiscussionIntake(intent='reply', message='只返回文本。')

    class Accounting:
        def __enter__(self):
            events.append(('accounting_enter',))

        def __exit__(self, *args):
            events.append(('accounting_exit',))

    monkeypatch.setattr(ports, 'get_usage_meter', lambda: Meter())
    monkeypatch.setattr(llm_instrumentation, 'install_provider_instrumentation', lambda: None)
    monkeypatch.setattr(llm_instrumentation, 'observe_agent_accounting', lambda: Accounting())
    monkeypatch.setattr(runtime_module, 'ModelApiStructuredRuntime', Runtime)
    route = AgentTaskRouteSnapshot(task_role=discussions.ROLE, source='global',
                                   runtime='model_api', model='test-model')

    reply = await discussions.run_message(ctx, route, 'fixed prompt', request_id='billing-id')

    runtime_event = next(item for item in events if item[0] == 'runtime')
    run_event = next(item for item in events if item[0] == 'run')
    set_event = next(item for item in events if item[0] == 'set')
    assert runtime_event[2]['strict_single_request'] is True
    assert callable(runtime_event[2]['result_observer'])
    assert callable(runtime_event[2]['request_started_observer'])
    assert run_event[1]['output_type'] is discussions.DiscussionIntake
    assert 'result_observer' not in run_event[1]
    assert not {'tools', 'toolsets', 'mcp_servers'} & run_event[1].keys()
    assert set_event[2]['resource_kind'] == 'script'
    assert set_event[2]['billing_metadata']['billing_task_type'] == 'creation_agent_discussion'
    assert set_event[2]['billing_metadata']['resource_subtype'] == 'creation_agent_discussion'
    assert set_event[2]['billing_metadata']['request_id'] == 'billing-id'
    assert reply.message == '只返回文本。'
    assert events[-1] == ('clear',)


async def test_codex_discussion_uses_strict_tool_disabled_runtime(ctx, monkeypatch):
    from novelvideo import llm_instrumentation, ports
    from novelvideo.text_task_runtime.models import AgentTaskRouteSnapshot
    from novelvideo.text_task_runtime import runtime as runtime_module

    events = []
    class Meter:
        def set_llm_usage_context(self, *args, **kwargs):
            events.append(('set', args, kwargs))
        def clear_llm_usage_context(self):
            events.append(('clear',))

    class Runtime:
        def __init__(self, route, **kwargs):
            events.append(('runtime', route, kwargs))
            self.kwargs = kwargs
        async def run_structured(self, **kwargs):
            events.append(('run', kwargs))
            self.kwargs['request_started_observer']()
            self.kwargs['result_observer'](object())
            return discussions.DiscussionIntake(intent='reply', message='只讨论方案。')

    class Accounting:
        def __enter__(self):
            events.append(('accounting_enter',))
            return {'status': 'unknown', 'events': []}
        def __exit__(self, *args):
            events.append(('accounting_exit',))

    monkeypatch.setattr(ports, 'get_usage_meter', lambda: Meter())
    monkeypatch.setattr(llm_instrumentation, 'install_provider_instrumentation', lambda: None)
    monkeypatch.setattr(llm_instrumentation, 'observe_agent_accounting', lambda: Accounting())
    monkeypatch.setattr(runtime_module, 'CodexStructuredRuntime', Runtime)
    route = AgentTaskRouteSnapshot(task_role=discussions.ROLE, source='global',
                                   runtime='codex', model='gpt-6-luna')

    reply = await discussions.run_message(ctx, route, 'fixed prompt', request_id='codex-discussion')

    runtime_event = next(item for item in events if item[0] == 'runtime')
    assert runtime_event[2]['strict_single_request'] is True
    assert runtime_event[2]['disable_execution_tools'] is True
    assert len([item for item in events if item[0] == 'run']) == 1
    assert events[0][2]['billing_metadata']['billing_task_type'] == discussions.BILLING_TASK
    assert reply.message == '只讨论方案。'
    assert events[-1] == ('clear',)


@pytest.mark.parametrize('failure_stage,expected', [
    ('before_request', ('not_submitted', 'runtime_initialization_error')),
    ('invalid_response', ('response_invalid', 'model_response_invalid')),
])
async def test_run_message_uses_runtime_observers_to_classify_failures(
        ctx, monkeypatch, failure_stage, expected):
    from novelvideo import llm_instrumentation, ports
    from novelvideo import config
    from novelvideo.text_task_runtime.models import AgentTaskRouteSnapshot
    from pydantic_ai.models.test import TestModel

    class Meter:
        def set_llm_usage_context(self, *args, **kwargs):
            pass

        def clear_llm_usage_context(self):
            pass

    class Accounting:
        def __enter__(self):
            pass

        def __exit__(self, *args):
            pass

    models = []
    monkeypatch.setattr(ports, 'get_usage_meter', lambda: Meter())
    monkeypatch.setattr(llm_instrumentation, 'install_provider_instrumentation', lambda: None)
    monkeypatch.setattr(llm_instrumentation, 'observe_agent_accounting', lambda: Accounting())
    if failure_stage == 'before_request':
        def fail_model_construction(*args, **kwargs):
            raise ValueError('API key=private initialization detail')

        monkeypatch.setattr(config, 'get_newapi_text_pydantic_model', fail_model_construction)
    else:
        class CountingTestModel(TestModel):
            def __init__(self):
                super().__init__(custom_output_text='not structured output')
                self.request_count = 0

            async def request(self, *args, **kwargs):
                self.request_count += 1
                return await super().request(*args, **kwargs)

        def make_test_model(*args, **kwargs):
            model = CountingTestModel()
            models.append(model)
            return model

        monkeypatch.setattr(config, 'get_newapi_text_pydantic_model', make_test_model)
    route = AgentTaskRouteSnapshot(task_role=discussions.ROLE, source='global',
                                   runtime='model_api', model='test-model')

    with pytest.raises(OperationError) as failure:
        await discussions.run_message(ctx, route, 'fixed prompt', request_id='diagnostic-id')

    diagnostic = failure.value.details['diagnostic']
    assert (diagnostic['outcome'], diagnostic['code']) == expected
    assert diagnostic['response_received'] is (failure_stage == 'invalid_response')
    assert diagnostic['http_status'] is None
    assert 'private initialization detail' not in str(failure.value)
    assert 'private initialization detail' not in json.dumps(failure.value.details)
    assert (models[0].request_count if models else 0) == (1 if failure_stage == 'invalid_response' else 0)


async def test_history_is_chronological_and_limited_to_last_100_entries(discussion_service, ready, monkeypatch):
    index = 0

    async def model(*args, **kwargs):
        return {'intent': 'reply', 'message': f'答复 {kwargs["request_id"]}'}

    monkeypatch.setattr(discussions, 'run_message', model)
    for index in range(51):
        await discussion_service.submit(_request(ready, request_id=f'request-{index}', message=f'问题 {index}'))
    history = await discussion_service.history()

    assert len(history['messages']) == 100
    assert history['messages'][0]['message'] == '问题 1'
    assert history['messages'][-1]['message'] == '答复 request-50'
    assert [item['created_at'] for item in history['messages']] == sorted(
        item['created_at'] for item in history['messages'])


async def test_late_reply_is_included_in_last_100_flattened_entries(discussion_service, ready, monkeypatch):
    import asyncio

    entered = asyncio.Event()
    release = asyncio.Event()

    async def model(ctx, route, prompt, *, request_id):
        if request_id == 'old-request':
            entered.set()
            await release.wait()
        return {'intent': 'reply', 'message': f'答复 {request_id}'}

    monkeypatch.setattr(discussions, 'run_message', model)
    old_request = asyncio.create_task(discussion_service.submit(
        _request(ready, request_id='old-request', message='最早的问题')))
    await entered.wait()

    for index in range(100):
        await discussion_service.submit(_request(
            ready, request_id=f'new-request-{index}', message=f'较新的问题 {index}'))

    release.set()
    await old_request
    history = await discussion_service.history()

    assert len(history['messages']) == 100
    assert history['messages'][-1]['request_id'] == 'old-request'
    assert history['messages'][-1]['role'] == 'assistant'
    assert history['messages'][-1]['message'] == '答复 old-request'
