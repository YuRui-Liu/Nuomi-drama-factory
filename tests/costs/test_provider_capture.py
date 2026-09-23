import httpx
import pytest

from novelvideo.costs.context import CostContext, cost_context
from novelvideo.costs.service import CostService
from novelvideo.costs.store import CostStore
from novelvideo.media_capabilities.runtime.runninghub_client import RunningHubClient
from novelvideo.media_capabilities.image.grsai import GrsaiClient
from novelvideo.media_capabilities.models import ImageGenerationRequest, MediaCapability


@pytest.fixture
def service(tmp_path):
    return CostService(CostStore(tmp_path / 'costs.db'))


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['runninghub', 'grsai'])
async def test_real_client_capture_replay_and_no_invented_fee(service, provider):
    requests = []
    def respond(request):
        requests.append(request)
        if provider == 'runninghub':
            return httpx.Response(200, json={'data': {'taskId': 'external', 'status': 'succeeded', 'cost': 42}})
        return httpx.Response(200, json={'id': 'external', 'status': 'succeeded', 'cost': 42})
    transport = httpx.MockTransport(respond)
    if provider == 'runninghub':
        client = RunningHubClient('secret', transport=transport, account_id='acct', cost_service=service,
                                  workflow_media={'wf': 'image'})
        submit = lambda: client.submit('wf', [])
        query = lambda: client.query('external')
    else:
        client = GrsaiClient(httpx.AsyncClient(base_url='https://provider.test', transport=transport),
                             account_id='acct', cost_service=service)
        submit = lambda: client.submit(ImageGenerationRequest(capability=MediaCapability.IMAGE_SINGLE,
                                       prompt='private prompt'), api_key='secret')
        query = lambda: client.query('external', api_key='secret')
    with cost_context(CostContext(project_id='p', media_type='image', attempt_id='a')):
        assert await submit() == 'external'
        await query()
        before = service.store.list_revisions('a')
        await query()
        assert service.store.list_revisions('a') == before
    attempt = service.store.get_attempt('a')
    assert attempt.project_id == 'p'
    assert attempt.usage == {'call': '1', 'item': '1'}
    assert attempt.execution_status == 'succeeded'
    assert service.store.get_cost('a').status == 'unpriced'
    assert len(service.store.list_attempts()) == 1
    await (client.close() if provider == 'runninghub' else client.http.aclose())


@pytest.mark.asyncio
async def test_codex_generation_retries_are_text_and_status_is_excluded(service, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from pydantic import BaseModel
    from novelvideo.knowledge_runtime import codex
    class Result(BaseModel):
        name: str
    monkeypatch.setattr(codex, '_create_codex_process', AsyncMock(return_value=object()))
    monkeypatch.setattr(codex, 'supervise_codex_process', AsyncMock(side_effect=[
        SimpleNamespace(completed_from_final_message=True, output='wrong'),
        SimpleNamespace(completed_from_final_message=True, output='{"name":"ok"}'),
    ]))
    backend = codex.CodexCliStructuredBackend(model='real-model', account_id='cli-slot', cost_service=service)
    with cost_context(CostContext(project_id='p', media_type='image', attempt_id='outer')):
        result = await backend.acreate_structured_output('private prompt', '', Result)
        assert result.name == 'ok'
        await codex._create_codex_process(['codex', 'login', 'status'], stdin=-1)
    attempts = service.store.list_attempts()
    assert len(attempts) == 2
    assert len({a.attempt_id for a in attempts}) == 2
    assert all(a.media_type == 'text' and a.model == 'real-model' and a.usage == {'call': '1'} for a in attempts)


def test_configuration_injects_account_and_workflow_media(monkeypatch):
    from types import SimpleNamespace
    from novelvideo.media_capabilities.runtime import configuration as c
    from novelvideo.media_capabilities.models import RunningHubWorkflowSettings
    monkeypatch.setattr(c, 'RunningHubClient', type('Client', (), {'DEFAULT_BASE_URL': 'https://example.test',
                        '__init__': lambda self, *a, **kw: setattr(self, 'options', kw)}))
    runtime = c.RunningHubRuntimeConfiguration(SimpleNamespace(id='real-account', base_url=None), 'secret',
                 RunningHubWorkflowSettings(image_upscale='100', tts_qwen3_voice_design='200'))
    options = runtime.create_client().options
    assert options['account_id'] == 'real-account'
    assert options['workflow_media']['100'] == 'image'
    assert options['workflow_media']['200'] == 'audio'


def make_client(provider, service, respond):
    transport = httpx.MockTransport(respond)
    if provider == 'runninghub':
        client = RunningHubClient('secret', transport=transport, account_id='acct', cost_service=service,
                                  workflow_media={'wf': 'image'})
        return client, lambda: client.submit('wf', []), lambda: client.query('external'), client.close
    client = GrsaiClient(httpx.AsyncClient(base_url='https://provider.test', transport=transport),
                         account_id='acct', cost_service=service)
    return client, lambda: client.submit(ImageGenerationRequest(capability=MediaCapability.IMAGE_SINGLE,
                   prompt='private'), api_key='secret'), lambda: client.query('external', api_key='secret'), client.http.aclose


def success(provider, status='succeeded'):
    if provider == 'runninghub':
        return httpx.Response(200, json={'data': {'taskId': 'external', 'status': status}})
    return httpx.Response(200, json={'id': 'external', 'status': status})


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['runninghub', 'grsai'])
async def test_prepare_failure_prevents_network(service, monkeypatch, provider):
    calls = []
    _, submit, _, close = make_client(provider, service, lambda r: calls.append(r) or success(provider))
    monkeypatch.setattr(service, 'prepare', lambda _: (_ for _ in ()).throw(OSError('disk full')))
    with cost_context(CostContext(project_id='p', media_type='image')):
        with pytest.raises(OSError, match='disk full'):
            await submit()
    assert calls == []
    await close()


@pytest.mark.asyncio
async def test_voice_wrapper_attaches_requested_characters_without_prompt(service):
    from types import SimpleNamespace
    from novelvideo.media_capabilities.tts.runninghub_voice_design import generate_qwen3_voice_sample
    def respond(request):
        if request.url.path == '/task/openapi/create':
            return success('runninghub')
        if request.url.path == '/openapi/v2/query':
            return httpx.Response(200, json={'data': {'status': 'succeeded', 'results': [{'url': 'https://files.test/voice.wav'}]}})
        return httpx.Response(200, content=b'RIFFvoice')
    client = RunningHubClient('secret', transport=httpx.MockTransport(respond), account_id='acct',
                 cost_service=service, workflow_media={'wf': 'audio'}, download_allowed_hosts=['files.test'])
    runtime = SimpleNamespace(workflow_id=lambda _: 'wf', create_client=lambda: client)
    with cost_context(CostContext(project_id='p', media_type='text')):
        await generate_qwen3_voice_sample(runtime, audition_text='你好', voice_description='private')
    attempt = service.store.list_attempts()[0]
    assert attempt.media_type == 'audio'
    assert attempt.usage == {'call': '1', 'character': '2'}
    assert 'private' not in attempt.model_dump_json()


@pytest.mark.asyncio
async def test_executor_passes_stable_attempt_context(service, tmp_path):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from novelvideo.media_capabilities.task_store import TaskStore
    from novelvideo.media_capabilities.runtime.executor import RunningHubExecutor
    from novelvideo.media_capabilities.models import WorkflowProfile
    @asynccontextmanager
    async def lease(*a):
        yield
    store = TaskStore(tmp_path / 'tasks.db')
    task = store.create_task(MediaCapability.IMAGE_SINGLE, 'request', {'id': 'impl'}, {'prompt': 'private'})
    attempt = store.start_attempt(task.id, 'acct')
    client = RunningHubClient('secret', transport=httpx.MockTransport(lambda r: success('runninghub')),
            account_id='acct', cost_service=service, workflow_media={'wf': 'image'})
    executor = RunningHubExecutor(store, client, None, SimpleNamespace(lease=lease))
    profile = WorkflowProfile(id='profile', version=1, workflow_id='wf', capabilities=[MediaCapability.IMAGE_SINGLE],
                              bindings={'prompt': {'node_id': '7', 'field': 'text'}})
    with cost_context(CostContext(project_id='p', media_type='text')):
        await executor.step(task.id, profile=profile, semantic_values={'prompt': 'private'})
    assert service.store.get_attempt(attempt.id).media_type == 'image'
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['runninghub', 'grsai'])
async def test_timeout_is_unknown_and_not_retried(service, provider):
    calls = []
    def respond(request):
        calls.append(request)
        raise httpx.ReadTimeout('ambiguous')
    _, submit, _, close = make_client(provider, service, respond)
    with cost_context(CostContext(project_id='p', media_type='image', attempt_id='a')):
        with pytest.raises(Exception):
            await submit()
    assert len(calls) == 1
    assert service.store.get_attempt('a').submission_status == 'unknown'
    await close()


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['runninghub', 'grsai'])
async def test_post_send_failure_preserves_id_and_query_repairs(service, monkeypatch, provider):
    calls = []
    _, submit, query, close = make_client(provider, service, lambda r: calls.append(r) or success(provider))
    original = service.submitted
    monkeypatch.setattr(service, 'submitted', lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    with cost_context(CostContext(project_id='p', media_type='image', attempt_id='a')):
        assert await submit() == 'external'
        monkeypatch.setattr(service, 'submitted', original)
        await query()
    attempt = service.store.get_attempt('a')
    assert attempt.external_id == 'external'
    assert attempt.execution_status == 'succeeded'
    assert sum('/create' in str(r.url) or '/generate' in str(r.url) for r in calls) == 1
    await close()


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['runninghub', 'grsai'])
async def test_resumed_query_cannot_mutate_other_project(service, provider):
    _, submit, _, close = make_client(provider, service, lambda r: success(provider, 'running'))
    with cost_context(CostContext(project_id='p', media_type='image', attempt_id='a')):
        await submit()
    await close()
    _, _, query, close = make_client(provider, service, lambda r: success(provider))
    with cost_context(CostContext(project_id='other', media_type='image', attempt_id='a')):
        await query()
    assert service.store.get_attempt('a').execution_status == 'running'
    with cost_context(CostContext(project_id='p', media_type='image')):
        await query()
    assert service.store.get_attempt('a').execution_status == 'succeeded'
    await close()


@pytest.mark.asyncio
async def test_grsai_connect_retry_counts_one_attempt(service, monkeypatch):
    from unittest.mock import AsyncMock
    from novelvideo.media_capabilities.image import grsai
    monkeypatch.setattr(grsai.asyncio, 'sleep', AsyncMock())
    calls = []
    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError('not sent')
        return success('grsai')
    _, submit, _, close = make_client('grsai', service, respond)
    with cost_context(CostContext(project_id='p', media_type='image')):
        await submit()
    assert len(calls) == 2
    assert len(service.store.list_attempts()) == 1
    await close()


@pytest.mark.asyncio
async def test_video_pipeline_semantic_duration_scope():
    from types import SimpleNamespace
    from novelvideo.costs.context import resolve_cost_context
    from novelvideo.media_capabilities.video.pipeline import H3VideoPipeline
    captured = []
    async def step(*a, **kw):
        captured.append(resolve_cost_context())
    pipeline = object.__new__(H3VideoPipeline)
    pipeline.executor = SimpleNamespace(step=step)
    with cost_context(CostContext(project_id='p', media_type='text')):
        await pipeline._step_with_cancellation('task', requested_seconds=7.5)
    assert captured[0].media_type == 'video'
    assert captured[0].usage['second'] == '7.5'


@pytest.mark.asyncio
async def test_failed_request_usage_remains_unpriced(service):
    from datetime import datetime, timezone
    from novelvideo.costs.models import PriceRule
    service.store.add_price_rule(PriceRule(id='r', version='1', media_type='image',
        starts_at=datetime(2020, 1, 1, tzinfo=timezone.utc), items=[dict(unit='item', unit_price='2')]))
    _, submit, _, close = make_client('runninghub', service, lambda r: success('runninghub'))
    with cost_context(CostContext(project_id='p', media_type='image', attempt_id='a')):
        await submit()
    await close()
    _, _, query, close = make_client('runninghub', service, lambda r: success('runninghub', 'failed'))
    with cost_context(CostContext(project_id='p', media_type='image')):
        await query()
    assert service.store.get_cost('a').status == 'unpriced'
    await close()


@pytest.mark.asyncio
async def test_project_independent_call_does_not_create_ledger(service):
    _, submit, query, close = make_client('runninghub', service, lambda r: success('runninghub'))
    await submit()
    await query()
    assert service.store.list_attempts() == []
    await close()


@pytest.mark.asyncio
@pytest.mark.parametrize('account_id, workflow_media', [(None, {'wf': 'image'}), ('acct', {})])
async def test_missing_attribution_aborts_before_send(service, account_id, workflow_media):
    calls = []
    client = RunningHubClient('secret', transport=httpx.MockTransport(lambda r: calls.append(r) or success('runninghub')),
                               account_id=account_id, workflow_media=workflow_media, cost_service=service)
    with cost_context(CostContext(project_id='p', media_type='text')):
        with pytest.raises(ValueError, match='attribution requires'):
            await client.submit('wf', [])
    assert calls == []
    await client.close()


@pytest.mark.asyncio
async def test_requested_usage_estimate_is_not_confirmed(service):
    from datetime import datetime, timezone
    from novelvideo.costs.models import PriceRule
    service.store.add_price_rule(PriceRule(id='r', version='1', media_type='image',
        starts_at=datetime(2020, 1, 1, tzinfo=timezone.utc), items=[dict(unit='item', unit_price='2')]))
    _, submit, query, close = make_client('runninghub', service, lambda r: success('runninghub'))
    with cost_context(CostContext(project_id='p', media_type='image', attempt_id='a')):
        await submit()
        await query()
    assert service.store.get_cost('a').status == 'estimated'
    assert service.store.get_cost('a').amount_micros == 2_000_000
    await close()


@pytest.mark.asyncio
async def test_resumed_stable_intent_repairs_post_send_storage_gap(service, monkeypatch):
    _, submit, _, close = make_client('runninghub', service, lambda r: success('runninghub'))
    original = service.submitted
    monkeypatch.setattr(service, 'submitted', lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    with cost_context(CostContext(project_id='p', media_type='image', attempt_id='a')):
        assert await submit() == 'external'
    await close()
    monkeypatch.setattr(service, 'submitted', original)
    _, _, query, close = make_client('runninghub', service, lambda r: success('runninghub'))
    with cost_context(CostContext(project_id='p', media_type='image', attempt_id='a')):
        await query()
    assert service.store.get_attempt('a').external_id == 'external'
    assert service.store.get_attempt('a').execution_status == 'succeeded'
    await close()
