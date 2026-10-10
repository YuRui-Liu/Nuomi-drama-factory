from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import case_reference as route
from .test_service import inputs, case


def test_project_permissions_owner_isolation_idempotency_and_cancel(tmp_path, monkeypatch):
    user = {'user_id': 'alice', 'editable': True}
    async def resolve(project, user, required_role):
        if required_role == 'editor' and not user['editable']:
            raise HTTPException(403, detail='read only')
        return (SimpleNamespace(runtime_dir=tmp_path / project),)
    monkeypatch.setattr(route, '_resolve_freezone_project', resolve)
    monkeypatch.setattr(route, '_limits', lambda: (5, 5))
    monkeypatch.setattr('novelvideo.case_reference.service.get_case', lambda _: case())
    backend = SimpleNamespace(enqueue_project_task=AsyncMock(return_value=SimpleNamespace(task_state=SimpleNamespace(task_id='t1'))),
                              cancel_project_task=AsyncMock())
    monkeypatch.setattr(route, 'get_task_backend', lambda: backend)
    monkeypatch.setattr(route, 'get_task_manager', lambda: SimpleNamespace(get_task_for_project=lambda *a, **k: None))
    app = FastAPI()
    app.include_router(route.router)
    app.dependency_overrides[get_api_user] = lambda: user
    client = TestClient(app)
    path = '/projects/project/freezone/case-reference/proposals'
    body = inputs(description='从桌面侧移至门口，以背影收尾。').model_dump(mode='json')
    one = client.post(path, json=body)
    assert one.status_code == 202
    proposal_id = one.json()['data']['id']
    assert client.post(path, json=body).json()['data']['id'] == proposal_id
    assert backend.enqueue_project_task.await_count == 1
    assert backend.enqueue_project_task.call_args.kwargs['task_type'] == 'case_reference'
    assert client.post(path, json={**body, 'description': '不同的完整画面描述。'}).status_code == 409
    user['editable'] = False
    assert client.post(path, json=body).status_code == 403
    assert client.post(f'{path}/{proposal_id}/cancel').status_code == 403
    user.update(user_id='bob', editable=True)
    assert client.get(f'{path}/{proposal_id}').status_code == 404
    user['user_id'] = 'alice'
    assert client.get(f'/projects/other/freezone/case-reference/proposals/{proposal_id}').status_code == 404
    assert client.post(f'{path}/{proposal_id}/cancel').json()['data']['status'] == 'cancelled'


def test_task_runner_is_text_only_registered():
    from novelvideo.task_backend.runners import case_reference
    from novelvideo.task_backend.registry import get_project_task_runner
    assert get_project_task_runner('case_reference') is not None
