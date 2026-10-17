import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from novelvideo.creation import discussions
from novelvideo.creation.operations import OperationError
from novelvideo.freezone.canvas_store import canvas_path
from .test_delegations import ctx, ready, service


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
        return {'message': '可以从人物关系和场景目标两方面展开。'}

    monkeypatch.setattr(discussions, 'run_message', model)
    result = await discussion_service.submit(_request(ready))

    assert result['status'] == 'completed'
    assert result['message'] == '可以从人物关系和场景目标两方面展开。'
    assert calls[0][2] == 'discussion-1'
    assert '开场 A' in calls[0][1]
    assert 'node_ids' not in calls[0][1]


async def test_same_request_replays_once_and_changed_payload_conflicts(discussion_service, ready, monkeypatch):
    calls = []

    async def model(*args, **kwargs):
        calls.append(1)
        return {'message': '已收到。'}

    monkeypatch.setattr(discussions, 'run_message', model)
    request = _request(ready)
    first = await discussion_service.submit(request)
    assert await discussion_service.submit(request) == first
    with pytest.raises(OperationError) as failure:
        await discussion_service.submit(_request(ready, message='不同内容'))

    assert failure.value.code == 'idempotency_conflict'
    assert calls == [1]


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
        return {'message': '不得向已撤销访问者返回的内容。'}

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
        return {'message': '已收到。'}

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
        return {'message': '完成。'}

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
            return discussions.DiscussionReply(message='只返回文本。')

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
    assert runtime_event[2] == {'strict_single_request': True}
    assert run_event[1]['output_type'] is discussions.DiscussionReply
    assert not {'tools', 'toolsets', 'mcp_servers'} & run_event[1].keys()
    assert set_event[2]['resource_kind'] == 'script'
    assert set_event[2]['billing_metadata']['billing_task_type'] == 'creation_agent_discussion'
    assert set_event[2]['billing_metadata']['resource_subtype'] == 'creation_agent_discussion'
    assert set_event[2]['billing_metadata']['request_id'] == 'billing-id'
    assert reply.message == '只返回文本。'
    assert events[-1] == ('clear',)


async def test_history_is_chronological_and_limited_to_last_100_entries(discussion_service, ready, monkeypatch):
    index = 0

    async def model(*args, **kwargs):
        return {'message': f'答复 {kwargs["request_id"]}'}

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
        return {'message': f'答复 {request_id}'}

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
