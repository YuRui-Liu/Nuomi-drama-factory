import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from novelvideo.text_task_runtime.models import AgentTaskRouteSnapshot
from novelvideo.text_task_runtime.runtime import build_text_task_runtime
from novelvideo.text_task_runtime import workbuddy
from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError
from novelvideo.knowledge_runtime.codex import StructuredImage


class Answer(BaseModel):
    value: str


def runtime():
    return build_text_task_runtime(AgentTaskRouteSnapshot(runtime='workbuddy', model='default-model',
        task_role='director_plan', source='global'))


def fake_cli(monkeypatch, *, stdout=b'', stderr=b'', returncode=0, communicate=None):
    """Stand in for the bundled CLI.

    The runtime hands the child a *file* for stdout (a pipe loses whatever the
    Node CLI has not flushed before process.exit()), so the fake writes its
    payload into that sink instead of returning it from ``communicate``.
    """

    captured = {}

    async def _spawn(*args, **kwargs):
        sink = kwargs.get('stdout')
        if stdout and hasattr(sink, 'write'):
            sink.write(stdout)
            sink.flush()
        proc = SimpleNamespace(returncode=returncode)
        proc.communicate = communicate or AsyncMock(return_value=(None, stderr))
        captured['proc'] = proc
        return proc

    spawn = AsyncMock(side_effect=_spawn)
    monkeypatch.setattr(workbuddy, 'workbuddy_command', lambda: '/app/workbuddy')
    monkeypatch.setattr(workbuddy.asyncio, 'create_subprocess_exec', spawn)
    return spawn, captured


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [b'{"structured_output":{"value":"ok"}}', b'{"result":"{\\"value\\":\\"ok\\"}"}', b'[{"type":"result","result":"{\\"value\\":\\"ok\\"}"}]'])
async def test_workbuddy_routes_to_cli_with_no_agent_tools(monkeypatch, payload):
    spawn, captured = fake_cli(monkeypatch, stdout=payload)
    result = await runtime().run_structured(prompt='task', system_prompt='system', output_type=Answer)
    assert result.value == 'ok'
    argv = spawn.call_args.args
    assert argv[0] == '/app/workbuddy'
    assert argv[argv.index('--tools') + 1] == ''
    assert '--strict-mcp-config' in argv and '--json-schema' not in argv
    assert b'system\n\ntask' in captured['proc'].communicate.call_args.args[0]


@pytest.mark.asyncio
async def test_workbuddy_stdout_is_read_from_a_file_not_a_pipe(monkeypatch):
    # Regression: a large payload must survive. The CLI truncates pipe writes at
    # the 64 KiB boundary when it exits, so the runtime must hand it a file.
    spawn, _ = fake_cli(monkeypatch, stdout=b'{"structured_output":{"value":"ok"}}')
    result = await runtime().run_structured(prompt='task', output_type=Answer)
    assert result.value == 'ok'
    sink = spawn.call_args.kwargs['stdout']
    assert hasattr(sink, 'write')
    assert spawn.call_args.kwargs['stdout'] is not asyncio.subprocess.PIPE


@pytest.mark.asyncio
async def test_workbuddy_extracts_json_wrapped_in_model_prose(monkeypatch):
    # The CLI's models often explain themselves and only then emit a fenced
    # ```json block; the whole message is not valid JSON.
    prose = ('I will compute exact offsets.\n\n```bash\npython3 - <<PY\n...\nPY\n```\n\n'
             'Here is the result:\n\n```json\n{"value":"ok"}\n```\n')
    fake_cli(monkeypatch, stdout=json.dumps(
        [{"type": 'result', "is_error": False, "result": prose}]).encode())
    result = await runtime().run_structured(prompt='task', output_type=Answer)
    assert result.value == 'ok'


@pytest.mark.asyncio
async def test_workbuddy_failure_does_not_expose_process_output(monkeypatch):
    fake_cli(monkeypatch, stdout=b'secret', stderr=b'secret', returncode=1)
    with pytest.raises(KnowledgeRuntimeError) as error:
        await runtime().run_structured(prompt='task', output_type=Answer)
    assert 'secret' not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('stderr,needle', [
    (b'429 Credits exhausted. Please visit https://www.codebuddy.ai/profile', '额度已耗尽'),
    (b'400 model [Hy3] service info not found (7576f122a995ecae34b7add0e9e36ae9/12df)', 'service info not found'),
    (b'400 the reasoning effort value is not supported by the current model', '推理强度'),
    (b'429 too many requests', '限流'),
])
async def test_workbuddy_empty_stdout_classifies_cli_failure(monkeypatch, stderr, needle):
    # The bundled CLI exits 0 and writes the real reason to stderr, so stdout is
    # empty. The runtime must not report that as a schema/format failure.
    fake_cli(monkeypatch, stdout=b'', stderr=stderr)
    with pytest.raises(KnowledgeRuntimeError) as error:
        await runtime().run_structured(prompt='task', output_type=Answer)
    assert error.value.code == 'WORKBUDDY_OUTPUT_EMPTY'
    assert needle in str(error.value)


@pytest.mark.asyncio
async def test_workbuddy_empty_stdout_without_known_error_stays_generic(monkeypatch):
    fake_cli(monkeypatch, stdout=b'', stderr=b'opaque provider noise')
    with pytest.raises(KnowledgeRuntimeError) as error:
        await runtime().run_structured(prompt='task', output_type=Answer)
    assert error.value.code == 'WORKBUDDY_OUTPUT_EMPTY'
    assert 'opaque provider noise' not in str(error.value)


@pytest.mark.asyncio
async def test_workbuddy_cancellation_cleans_up_process(monkeypatch):
    _, captured = fake_cli(monkeypatch, communicate=AsyncMock(side_effect=asyncio.CancelledError))
    terminate = AsyncMock()
    monkeypatch.setattr(workbuddy, 'terminate_process_tree', terminate)
    with pytest.raises(asyncio.CancelledError):
        await runtime().run_structured(prompt='task', output_type=Answer)
    terminate.assert_awaited_once_with(captured['proc'])


@pytest.mark.asyncio
async def test_workbuddy_sends_images_through_stream_json(monkeypatch):
    monkeypatch.setattr(workbuddy, 'workbuddy_command', lambda: '/app/workbuddy')
    output = b'{"type":"system","subtype":"init"}\n{"type":"result","result":"{\\"value\\":\\"ok\\"}"}\n'
    proc = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(output, b'')))
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(workbuddy.asyncio, 'create_subprocess_exec', spawn)
    image = StructuredImage(b'image-bytes', 'image/png')
    result = await runtime().run_structured(prompt='task', output_type=Answer, images=[image])
    assert result.value == 'ok'
    argv = spawn.call_args.args
    assert argv[argv.index('--input-format') + 1] == 'stream-json'
    assert argv[argv.index('--output-format') + 1] == 'stream-json'
    message = json.loads(proc.communicate.call_args.args[0])
    assert message['message']['content'][1] == {
        'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png',
                                    'data': base64.b64encode(image.data).decode('ascii')},
    }


@pytest.mark.asyncio
async def test_workbuddy_rejects_invalid_image_before_cli():
    with pytest.raises(ValueError, match='StructuredImage'):
        await runtime().run_structured(prompt='task', output_type=Answer, images=[object()])


@pytest.mark.asyncio
async def test_workbuddy_forwards_model_and_max_effort_to_cli(monkeypatch):
    spawn, _ = fake_cli(monkeypatch, stdout=b'{"structured_output":{"value":"ok"}}')
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
