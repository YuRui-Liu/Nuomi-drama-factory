from fastapi import FastAPI
from fastapi.testclient import TestClient
import json
from types import SimpleNamespace

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import technique_library


def test_case_routes_auth_and_paging():
    app = FastAPI()
    app.include_router(technique_library.router, prefix='/api/v1')
    user = {'user_id': 'alice'}
    app.dependency_overrides[get_api_user] = lambda: user
    client = TestClient(app)
    assert client.get('/api/v1/techniques/cases?limit=101').status_code == 422
    assert client.get('/api/v1/techniques/cases?offset=-1').status_code == 422
    user.clear()
    assert client.get('/api/v1/techniques/cases').status_code == 401
    assert client.get('/api/v1/techniques/cases/missing').status_code == 401


def test_case_routes_fail_closed(tmp_path, monkeypatch):
    app = FastAPI()
    app.include_router(technique_library.router, prefix='/api/v1')
    app.dependency_overrides[get_api_user] = lambda: {'id': 'alice'}
    monkeypatch.setattr(technique_library.case_catalog, 'CATALOG_PATH', tmp_path / 'missing.json')
    client = TestClient(app)
    for path in ('/cases', '/cases/missing'):
        assert client.get('/api/v1/techniques' + path).status_code == 503


def test_case_list_detail_and_related_cards(tmp_path, monkeypatch):
    app = FastAPI()
    app.include_router(technique_library.router, prefix='/api/v1')
    app.dependency_overrides[get_api_user] = lambda: {'id': 'alice'}
    case = {'id': 'h3-0123456789abcdef', 'title': '动作', 'summary': '研究动作节奏。',
            'use_cases': ['action'], 'provenance': 'author',
            'sources': [{'repository': 'test/repo', 'revision': 'a'*40, 'path': 'a.json',
                         'upstream_id': 'one', 'url': 'https://example.com/a'}]}
    path = tmp_path / 'catalog.json'
    path.write_text(json.dumps({'schema_version': '1.0', 'sources': [], 'report': {}, 'cases': [case]}))
    monkeypatch.setattr(technique_library.case_catalog, 'CATALOG_PATH', path)
    monkeypatch.setattr(technique_library, 'list_techniques', lambda: [SimpleNamespace(id='action-card', case_ids=[case['id']])])
    client = TestClient(app)
    listed = client.get('/api/v1/techniques/cases?q=动作&use_case=action&provenance=author&limit=1').json()
    assert listed['ok'] is True and listed['data']['total'] == 1
    assert listed['data']['items'][0]['related_technique_ids'] == ['action-card']
    detail = client.get('/api/v1/techniques/cases/' + case['id']).json()['data']
    assert detail['related_technique_ids'] == ['action-card']
    assert client.get('/api/v1/techniques/cases/missing').status_code == 404
    assert client.get('/api/v1/techniques/cases?offset=1').json()['data']['items'] == []
