from types import SimpleNamespace
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from novelvideo.api.routes import agent_teams as routes
from novelvideo.api.auth import get_api_user


def test_scope_permissions_and_authenticated_library(tmp_path, monkeypatch):
    calls = []
    async def scope(project, user, required_role):
        calls.append((user['username'], required_role))
        if project == 'forbidden':
            raise HTTPException(403, 'denied')
        return SimpleNamespace(state_dir=str(tmp_path / 'canonical'), ctx=SimpleNamespace(project_id='canonical'), username='different-owner')
    monkeypatch.setattr(routes, 'resolve_project_scope', scope)
    monkeypatch.setattr(routes, 'get_user_base_dir', lambda username: tmp_path / username)
    app = FastAPI()
    app.include_router(routes.router, prefix='/api/v1')
    app.dependency_overrides[get_api_user] = lambda: {'username': 'alice'}
    client = TestClient(app)
    assert client.get('/api/v1/projects/one/agent-team').status_code == 200
    assert client.put('/api/v1/projects/one/agent-team/draft', json={'data': {}, 'expected_revision': 0}).status_code == 200
    assert calls == [('alice', 'viewer'), ('alice', 'editor')]
    assert client.get('/api/v1/projects/forbidden/agent-team').status_code == 403
    assert client.put('/api/v1/projects/one/agent-team/draft', json={'data': {}, 'expected_revision': 0}).status_code == 409
    assert client.put('/api/v1/projects/one/agent-team/draft', json={'data': {'template': {}}, 'expected_revision': 1}).status_code == 422
    assert client.get('/api/v1/projects/alias/agent-team').json()['draft']['project_id'] == 'canonical'
    assert client.put('/api/v1/projects/one/agent-team/draft', json={'data': {}, 'expected_revision': True}).status_code == 422
    resource = client.put('/api/v1/agent-team-resources/r', json={'data': {'revision': 1, 'kind': 'skill', 'content': 'hello', 'owner': 'forged'}, 'expected_revision': 0})
    assert resource.status_code == 200
    assert resource.json()['owner'] == 'alice'
    assert len(resource.json()['content_hash']) == 64
