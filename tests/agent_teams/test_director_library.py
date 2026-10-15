from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from novelvideo.agent_teams.store import AgentTeamStore, RevisionConflict
from novelvideo.creative_studios.director_library import DirectorInput, DirectorLibrary
from novelvideo.api.routes import director_library as routes
from novelvideo.api.auth import get_api_user


def data(**overrides):
    return DirectorInput.model_validate({'name': ' 我的方法 ', 'creative_types': [' 情感 ', '情感'],
                                        'preferences': {'camera_motion': '缓推'}, **overrides})


def test_versions_ownership_tombstone_and_independent_copies(tmp_path):
    store = AgentTeamStore(tmp_path / 'personal.db')
    first = DirectorLibrary(store, 'alice')
    other = DirectorLibrary(store, 'bob')
    saved = first.save(data())
    assert saved.name == '我的方法'
    assert saved.creative_types == ['情感']
    assert DirectorLibrary(AgentTeamStore(tmp_path / 'personal.db'), 'alice').list()[0] == saved
    assert other.list() == []
    with pytest.raises(KeyError):
        other.save(data(), 1, saved.id)
    project_snapshot = deepcopy(saved.preferences.model_dump())
    updated = first.save(data(preferences={'camera_motion': '固定'}), 1, saved.id)
    assert updated.revision == 2
    with pytest.raises(RevisionConflict):
        first.save(data(), 1, saved.id)
    with pytest.raises(RevisionConflict):
        first.delete(saved.id, 1)
    first.delete(saved.id, 2)
    assert first.list() == []
    assert project_snapshot['camera_motion'] == '缓推'
    assert saved.preferences.camera_motion == '缓推'
    with pytest.raises(KeyError):
        first.save(data(), 3, saved.id)


@pytest.mark.parametrize('invalid', [{'name': ' '}, {'name': 'x' * 101}, {'description': 'x' * 1001},
    {'tags': ['x' * 31]}, {'tags': [' ']}, {'creative_types': [str(x) for x in range(21)]},
    {'preferences': {'allow_adaptation': True}}, {'preferences': {'method': 'x' * 100001}},
    {'preferences': {'pace': 42}}, {'owner': 'bob'}])
def test_validation(invalid):
    with pytest.raises(ValidationError):
        data(**invalid)


def test_authenticated_api_cross_project_and_conflicts(tmp_path, monkeypatch):
    import novelvideo.api.routes.agent_teams as teams
    monkeypatch.setattr(teams, 'get_user_base_dir', lambda username: tmp_path / username)
    app = FastAPI()
    app.include_router(routes.router, prefix='/api/v1')
    user = {'username': 'alice'}
    app.dependency_overrides[get_api_user] = lambda: user
    with TestClient(app) as client:
        payload = data().model_dump()
        response = client.post('/api/v1/director-library', json=payload)
        assert response.status_code == 201
        entry = response.json()['data']
        # Global endpoint is independent of the selected project.
        assert client.get('/api/v1/director-library?project=second').json()['data'][0]['id'] == entry['id']
        url = '/api/v1/director-library/' + entry['id']
        assert client.put(url, json={**payload, 'expected_revision': 1}).status_code == 200
        assert client.put(url, json={**payload, 'expected_revision': 1}).status_code == 409
        user['username'] = 'bob'
        assert client.get('/api/v1/director-library').json()['data'] == []
        assert client.delete(url + '?expected_revision=2').status_code == 404
        user['username'] = 'alice'
        assert client.delete(url + '?expected_revision=2').status_code == 200
        assert client.get('/api/v1/director-library').json()['data'] == []
        user['credential_kind'] = 'agent_session'
        assert client.get('/api/v1/director-library').status_code == 403
