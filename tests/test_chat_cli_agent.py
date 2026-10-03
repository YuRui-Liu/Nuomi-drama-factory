import asyncio
from types import SimpleNamespace

import pytest


@pytest.fixture
def engine(monkeypatch, tmp_path):
    from novelvideo.chat import cli_agent as mod
    monkeypatch.setenv('NOVELVIDEO_STATE_DIR', str(tmp_path))
    state = SimpleNamespace(prompts=[], routes=[], calls=[], validations=0, exits=0, answers=[], result={'title': 'roundtrip-title'})
    class Session:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): state.exits += 1
        async def validate(self): state.validations += 1
        def specs(self): return [{'name': 'inspect_project', 'parameters': {'type': 'object'}}]
        async def call(self, name, arguments):
            state.calls.append((name, arguments))
            return state.result
    class Runtime:
        async def run_structured(self, **kwargs):
            state.prompts.append(kwargs['prompt'])
            answer = state.answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            if isinstance(answer, asyncio.Event):
                await answer.wait()
            return kwargs['output_type'].model_validate(answer)
    def build(route):
        state.routes.append(route)
        return Runtime()
    monkeypatch.setattr(mod, 'AgentToolSession', Session)
    monkeypatch.setattr(mod, 'build_text_task_runtime', build)
    return mod, state


async def collect(thread, prompt='hello'):
    return [event async for event in thread.stream(prompt)]


@pytest.mark.parametrize('backend', ['codex', 'workbuddy', 'deepseek_harness'])
async def test_routes_and_tool_roundtrip(engine, backend):
    mod, state = engine
    state.answers = [{'message': '', 'tool_calls': [{'name': 'inspect_project', 'arguments_json': '{}'}]}, {'message': 'done', 'tool_calls': []}]
    events = await collect(mod.CliAgentThread('alice', 'project', 'p1', backend, 'model'))
    assert events[-1].text == 'done'
    assert state.routes[0].runtime == backend
    assert state.validations == 2
    assert state.calls == [('inspect_project', {})]
    assert 'roundtrip-title' in state.prompts[1]
    assert state.exits == 1


async def test_history_and_scope(engine):
    mod, state = engine
    state.answers = [{'message': 'remember-this', 'tool_calls': []}] * 3
    first = mod.CliAgentThread('alice', 'project', 'p1', 'codex', 'model')
    await collect(first)
    same = mod.CliAgentThread('alice', 'project', 'p1', 'codex', 'model')
    await collect(same)
    other = mod.CliAgentThread('alice', 'project', 'p2', 'codex', 'model')
    await collect(other)
    assert first.id == same.id != other.id
    assert 'remember-this' in state.prompts[1]
    assert 'remember-this' not in state.prompts[2]


async def test_cancel_cleans_resources(engine):
    mod, state = engine
    state.answers = [asyncio.Event()]
    thread = mod.CliAgentThread('alice', 'project', 'p1', 'codex', 'model')
    task = asyncio.create_task(collect(thread))
    while not state.prompts:
        await asyncio.sleep(0)
    assert await mod.cancel_user('alice')
    assert task.cancelled()
    assert state.exits == 1
    assert not await mod.cancel_user('alice')


@pytest.mark.parametrize('calls', [[{'name': 'unknown', 'arguments_json': '{}'}], [{'name': 'inspect_project', 'arguments_json': 'broken'}], [{'name': 'inspect_project', 'arguments_json': '{}'}] * 5])
async def test_invalid_calls_fail_without_execution(engine, calls):
    mod, state = engine
    state.answers = [{'message': 'success', 'tool_calls': calls}]
    with pytest.raises((ValueError, RuntimeError)):
        await collect(mod.CliAgentThread('alice', 'project', 'p1', 'codex', 'model'))
    assert not state.calls


async def test_step_budget(engine):
    mod, state = engine
    state.answers = [{'message': '', 'tool_calls': [{'name': 'inspect_project', 'arguments_json': '{}'}]}] * 20
    with pytest.raises(RuntimeError, match='limit'):
        await collect(mod.CliAgentThread('alice', 'project', 'p1', 'codex', 'model'))
    assert len(state.prompts) == 12


async def test_busy_user_and_close(engine):
    mod, state = engine
    state.answers = [asyncio.Event()]
    thread = mod.CliAgentThread('alice', 'project', 'p1', 'codex', 'model')
    task = asyncio.create_task(collect(thread))
    while not state.prompts:
        await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match='busy'):
        await collect(mod.CliAgentThread('alice', 'project', 'p2', 'workbuddy', 'model'))
    await thread.close()
    assert task.cancelled()
    assert state.exits == 1


async def test_history_is_bounded_and_credentials_redacted(engine):
    mod, state = engine
    state.answers = [{'message': 'done', 'tool_calls': []}]
    thread = mod.CliAgentThread('alice', 'project', 'p1', 'codex', 'model')
    await collect(thread, 'api_key=secretvalue Bearer credentialvalue ' + 'x' * 100_000)
    assert 'secretvalue' not in state.prompts[0]
    assert 'credentialvalue' not in state.prompts[0]
    assert len(state.prompts[0]) < 10_000
    assert thread._history_path.stat().st_size < mod.MAX_HISTORY_BYTES


async def test_adapter_failure_has_no_fallback_or_credentials(engine):
    mod, state = engine
    state.answers = [RuntimeError('sensitive-provider-credential')]
    with pytest.raises(RuntimeError, match='no fallback') as error:
        await collect(mod.CliAgentThread('alice', 'project', 'p1', 'codex', 'model'))
    assert 'sensitive-provider-credential' not in str(error.value)
    assert len(state.routes) == 1
    assert state.exits == 1


def test_decision_schema_is_closed(engine):
    mod, _ = engine
    schema = mod.Decision.model_json_schema()
    assert schema['additionalProperties'] is False
    assert schema['$defs']['ToolCall']['additionalProperties'] is False
    assert set(schema['required']) == {'message', 'tool_calls'}
    assert set(schema['$defs']['ToolCall']['required']) == {'name', 'arguments_json'}


async def test_current_project_must_match_immutable_scope(engine):
    mod, state = engine
    thread = mod.CliAgentThread('alice', 'project', 'p1', 'codex', 'model')
    with pytest.raises(ValueError, match='scope'):
        _ = [event async for event in thread.stream('hello', current_project='p2')]
    assert not state.routes
    state.answers = [{'message': 'done', 'tool_calls': []}]
    events = [event async for event in thread.stream('hello', current_project='p1')]
    assert events[-1].type == 'complete'


async def test_tool_result_emits_raw_and_stops_on_error(engine):
    mod, state = engine
    state.result = '{"ok":false,"error":"Task submission failed"}'
    state.answers = [{'message': '', 'tool_calls': [{'name': 'inspect_project', 'arguments_json': '{}'}]}]
    events = []
    with pytest.raises(RuntimeError, match='Tool failed'):
        async for event in mod.CliAgentThread('alice', 'project', 'p1', 'codex', 'model').stream('hello'):
            events.append(event)
    assert any(event.raw and 'Task submission failed' in str(event.raw) for event in events)
    assert not any(event.type == 'complete' for event in events)


async def test_tool_raw_preserves_large_ui_payload(engine):
    mod, state = engine
    state.result = {'padding': 'x' * 9000, 'ui_spec': {'type': 'test', 'root': 'root', 'elements': {}}}
    state.answers = [{'message': '', 'tool_calls': [{'name': 'inspect_project', 'arguments_json': '{}'}]}, {'message': 'done', 'tool_calls': []}]
    events = await collect(mod.CliAgentThread('alice', 'project', 'p1', 'codex', 'model'))
    assert any(event.raw and 'ui_spec' in str(event.raw) for event in events)


async def test_history_load_failure_does_not_leave_user_busy(engine, monkeypatch):
    mod, state = engine
    thread = mod.CliAgentThread('alice', 'project', 'p1', 'codex', 'model')
    def fail():
        raise TypeError('corrupt history')
    monkeypatch.setattr(thread, '_load', fail)
    with pytest.raises(TypeError):
        await collect(thread)
    state.answers = [{'message': 'done', 'tool_calls': []}]
    events = await collect(mod.CliAgentThread('alice', 'project', 'p1', 'codex', 'model'))
    assert events[-1].type == 'complete'
