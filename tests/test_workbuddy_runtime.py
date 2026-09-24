import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from novelvideo.text_task_runtime.models import AgentTaskRouteSnapshot
from novelvideo.text_task_runtime.runtime import build_text_task_runtime
from novelvideo.text_task_runtime import workbuddy
from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError


class Answer(BaseModel):
    value: str


def runtime():
    return build_text_task_runtime(AgentTaskRouteSnapshot(runtime='workbuddy', model='default-model',
        task_role='director_plan', source='global'))


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [b'{"structured_output":{"value":"ok"}}', b'{"result":"{\\"value\\":\\"ok\\"}"}', b'[{"type":"result","result":"{\\"value\\":\\"ok\\"}"}]'])
async def test_workbuddy_routes_to_cli_with_no_agent_tools(monkeypatch, payload):
    monkeypatch.setattr(workbuddy, 'workbuddy_command', lambda: '/app/workbuddy')
    proc = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(payload, b'')))
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(workbuddy.asyncio, 'create_subprocess_exec', spawn)
    result = await runtime().run_structured(prompt='task', system_prompt='system', output_type=Answer)
    assert result.value == 'ok'
    argv = spawn.call_args.args
    assert argv[0] == '/app/workbuddy'
    assert argv[argv.index('--tools') + 1] == ''
    assert '--strict-mcp-config' in argv and '--json-schema' not in argv
    assert b'system\n\ntask' in proc.communicate.call_args.args[0]


@pytest.mark.asyncio
async def test_workbuddy_failure_does_not_expose_process_output(monkeypatch):
    monkeypatch.setattr(workbuddy, 'workbuddy_command', lambda: '/app/workbuddy')
    proc = SimpleNamespace(returncode=1, communicate=AsyncMock(return_value=(b'secret', b'secret')))
    monkeypatch.setattr(workbuddy.asyncio, 'create_subprocess_exec', AsyncMock(return_value=proc))
    with pytest.raises(KnowledgeRuntimeError) as error:
        await runtime().run_structured(prompt='task', output_type=Answer)
    assert 'secret' not in str(error.value)


@pytest.mark.asyncio
async def test_workbuddy_cancellation_cleans_up_process(monkeypatch):
    monkeypatch.setattr(workbuddy, 'workbuddy_command', lambda: '/app/workbuddy')
    proc = SimpleNamespace(returncode=None, communicate=AsyncMock(side_effect=asyncio.CancelledError))
    monkeypatch.setattr(workbuddy.asyncio, 'create_subprocess_exec', AsyncMock(return_value=proc))
    terminate = AsyncMock()
    monkeypatch.setattr(workbuddy, 'terminate_process_tree', terminate)
    with pytest.raises(asyncio.CancelledError):
        await runtime().run_structured(prompt='task', output_type=Answer)
    terminate.assert_awaited_once_with(proc)


@pytest.mark.asyncio
async def test_images_are_never_silently_discarded():
    with pytest.raises(KnowledgeRuntimeError, match='图片输入'):
        await runtime().run_structured(prompt='task', output_type=Answer, images=[object()])


@pytest.mark.asyncio
async def test_workbuddy_forwards_model_and_max_effort_to_cli(monkeypatch):
    monkeypatch.setattr(workbuddy, 'workbuddy_command', lambda: '/app/workbuddy')
    proc = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b'{"structured_output":{"value":"ok"}}', b'')))
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(workbuddy.asyncio, 'create_subprocess_exec', spawn)
    route = AgentTaskRouteSnapshot(runtime='workbuddy', model='my-workbuddy-model', reasoning_effort='max',
                                   task_role='director_plan', source='global')
    result = await build_text_task_runtime(route).run_structured(prompt='task', output_type=Answer)
    assert result.value == 'ok'
    argv = spawn.call_args.args
    assert argv[argv.index('--model') + 1] == 'my-workbuddy-model'
    assert argv[argv.index('--effort') + 1] == 'max'


@pytest.mark.asyncio
@pytest.mark.parametrize('effort,expected', [('max', None), ('high', 'high')])
async def test_model_api_normalizes_reasoning_effort(monkeypatch, effort, expected):
    from novelvideo.text_task_runtime.runtime import ModelApiStructuredRuntime

    captured = {}
    monkeypatch.setattr('novelvideo.config.get_newapi_text_pydantic_model', lambda *args, **kwargs: 'fake-model')

    def factory(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(output=Answer(value='ok'))))

    route = AgentTaskRouteSnapshot(runtime='model_api', model='deepseek-v4-flash', reasoning_effort=effort,
                                   task_role='director_plan', source='global')
    result = await ModelApiStructuredRuntime(route, agent_factory=factory).run_structured(
        prompt='task', output_type=Answer)
    assert result.value == 'ok'
    settings = captured.get('model_settings')
    if expected is None:
        # "max" 是 WorkBuddy 专属档位，OpenAI 兼容接口没有对应值，必须丢弃。
        assert settings is None
    else:
        assert settings == {'openai_reasoning_effort': expected}
