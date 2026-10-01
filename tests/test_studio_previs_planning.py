from types import SimpleNamespace

import pytest

from novelvideo.creative_studios.previs import PrevisScene
from novelvideo.creative_studios.previs_planning import claim_plan, plan_scene


def scene():
    return PrevisScene.model_validate(dict(actors=[dict(id='a', name='演员', humanoid=True, position=[0, 0, 0], yaw=0, characterRef='演员')], clips=[], camera=[dict(id='c', time=0, position=[8, 6, 10], target=[0, 1, 0])], light=dict(yaw=30, intensity=1), props=[]))


@pytest.mark.asyncio
async def test_natural_language_uses_structured_runtime_and_preserves_identity():
    original = scene()
    generated = original.model_dump(mode='json')
    generated['clips'] = [dict(id='walk', actorId='a', action='walk', start=0, duration=3, target=[4, 0, 2], yaw=0)]
    calls = []
    async def run_structured(**kwargs):
        calls.append(kwargs)
        return {'scene': generated}
    result = await plan_scene(original, '让演员慢慢走向右前方', SimpleNamespace(run_structured=run_structured))
    assert result.clips[0].target == (4, 0, 2)
    assert len(calls) == 1 and '让演员慢慢走向右前方' in calls[0]['prompt']
    generated['actors'][0]['characterRef'] = 'other'
    with pytest.raises(ValueError, match='身份'):
        await plan_scene(original, '不要换角色', SimpleNamespace(run_structured=run_structured))


def test_plan_claim_retries_do_not_repeat_paid_submission(tmp_path):
    path = tmp_path / 'studio.db'
    assert claim_plan(path, 'request', 'fingerprint')
    assert not claim_plan(path, 'request', 'fingerprint')
    with pytest.raises(ValueError, match='请求标识'):
        claim_plan(path, 'request', 'different')


def test_plan_endpoint_requires_scope_and_enqueues_same_request_once(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from novelvideo.api.routes import studio_previs_planning as route
    from novelvideo.api.auth import get_api_user
    app = FastAPI()
    app.include_router(route.router)
    app.dependency_overrides[get_api_user] = lambda: {'id': 'tester', 'scopes': []}
    url = '/projects/p/studios/previs-tools/plans/' + 'a' * 32
    body = {'scene': scene().model_dump(mode='json'), 'instruction': '慢慢走向右边'}
    assert TestClient(app).post(url, json=body).status_code == 403
    app.dependency_overrides[get_api_user] = lambda: {'id': 'tester', 'scopes': ['tasks:submit']}
    ctx = SimpleNamespace(project_id='p', state_dir=tmp_path)
    async def resolve(*args, **kwargs):
        return ctx
    calls = []
    async def enqueue(*args, **kwargs):
        calls.append(kwargs)
    async def status(*args):
        return {'status': 'queued', 'task_id': 'task'}
    monkeypatch.setattr(route, '_resolve', resolve)
    monkeypatch.setattr(route, '_status', status)
    monkeypatch.setattr('novelvideo.ports.get_task_backend', lambda: SimpleNamespace(enqueue_project_task=enqueue))
    client = TestClient(app)
    assert client.post(url, json=body).json()['data']['reused'] is False
    assert client.post(url, json=body).json()['data']['reused'] is True
    assert len(calls) == 1
    assert calls[0]['task_type'] == 'studio_previs_plan'
    assert client.post(url, json={**body, 'instruction': '不同输入'}).status_code == 409


@pytest.mark.asyncio
async def test_runner_persists_review_scene_and_reuses_result_without_another_model_call(tmp_path, monkeypatch):
    from novelvideo.creative_studios.previs_planning import run_previs_plan
    from novelvideo.creative_studios.store import StudioStore
    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        return {'scene': scene().model_dump(mode='json')}
    monkeypatch.setattr('novelvideo.text_task_runtime.runtime.current_text_task_runtime', lambda: SimpleNamespace(run_structured=generate))
    ctx = SimpleNamespace(project_id='p', state_dir=tmp_path)
    envelope = {'payload': {'project_id': 'p', 'scene': scene().model_dump(mode='json'), 'instruction': '保留场景', 'result_document_id': 'previs-plan-test'}}
    first = await run_previs_plan(envelope, ctx)
    second = await run_previs_plan(envelope, ctx)
    assert first['status'] == 'review_required'
    assert second['reused'] is True
    assert len(calls) == 1
    assert StudioStore(tmp_path / 'creative-studios.db').get('previs', 'previs-plan-test')['data']['actors'][0]['characterRef'] == '演员'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure_stage', ['provider', 'persist'])
async def test_runner_does_not_repeat_paid_work_after_unknown_result(tmp_path, monkeypatch, failure_stage):
    from novelvideo.creative_studios.previs_planning import run_previs_plan
    from novelvideo.creative_studios.store import StudioStore
    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        if failure_stage == 'provider':
            raise ConnectionError('response lost after provider accepted the request')
        return {'scene': scene().model_dump(mode='json')}
    monkeypatch.setattr('novelvideo.text_task_runtime.runtime.current_text_task_runtime', lambda: SimpleNamespace(run_structured=generate))
    if failure_stage == 'persist':
        def fail_save(*args, **kwargs):
            raise ConnectionError('storage unavailable after successful model response')
        monkeypatch.setattr(StudioStore, 'save', fail_save)
    ctx = SimpleNamespace(project_id='p', state_dir=tmp_path)
    envelope = {'scope': 'previs:uncertain', 'payload': {'project_id': 'p', 'scene': scene().model_dump(mode='json'), 'instruction': '走近镜头', 'result_document_id': 'previs-plan-uncertain'}}
    with pytest.raises(ConnectionError):
        await run_previs_plan(envelope, ctx)
    with pytest.raises(ValueError, match='结果未知'):
        await run_previs_plan(envelope, ctx)
    assert len(calls) == 1
