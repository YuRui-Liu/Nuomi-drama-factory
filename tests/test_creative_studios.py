from concurrent.futures import ThreadPoolExecutor

import pytest

from novelvideo.creative_studios.store import StudioStore, RevisionConflict


def test_revision_history_and_optimistic_concurrency(tmp_path):
    store = StudioStore(tmp_path / 'studios.db')
    first = store.save('intro', 'draft-1', '片头', {'title': '旧名'}, 0)
    assert first['revision'] == 1
    second = store.save('intro', 'draft-1', '片头', {'title': '新名'}, 1)
    assert second['revision'] == 2
    with pytest.raises(RevisionConflict):
        store.save('intro', 'draft-1', '覆盖', {}, 1)
    assert store.get('intro', 'draft-1')['data']['title'] == '新名'
    assert store.history('intro', 'draft-1')[0]['data']['title'] == '旧名'


def test_kind_and_project_isolation_and_missing(tmp_path):
    store = StudioStore(tmp_path / 'a.db')
    store.save('previs', 'same', '预演', {'clips': []}, 0)
    assert store.get('intro', 'same') is None
    assert store.list('intro') == []
    assert StudioStore(tmp_path / 'b.db').list('previs') == []
    assert store.list('previs')[0]['id'] == 'same'


@pytest.mark.parametrize('kind,id', [('wrong', 'id'), ('intro', '../x'), ('intro', ''), ('intro', 'a/b')])
def test_reject_invalid_identifiers(tmp_path, kind, id):
    with pytest.raises(ValueError):
        StudioStore(tmp_path / 'studio.db').save(kind, id, '草稿', {}, 0)


def test_only_one_competing_save_wins(tmp_path):
    path = tmp_path / 'studio.db'
    StudioStore(path).save('director', 'draft', '导演', {}, 0)
    def update(i):
        try:
            StudioStore(path).save('director', 'draft', str(i), {}, 1)
            return True
        except RevisionConflict:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(update, [1, 2])) == 1


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import creative_studios as routes
    app = FastAPI()
    app.include_router(routes.router)
    user = {'role': 'editor'}
    app.dependency_overrides[get_api_user] = lambda: user
    async def resolve(project, user, required_role='viewer'):
        if project == 'foreign' or (required_role == 'editor' and user['role'] == 'viewer'):
            raise HTTPException(403)
        return SimpleNamespace(state_dir=tmp_path / project)
    monkeypatch.setattr(routes, 'resolve_project_scope', resolve)
    return TestClient(app), user


def test_api_save_conflict_permissions_and_history(client):
    c, user = client
    url = '/projects/p/studios/intro/title'
    body = {'name': '片头', 'data': {'title': '山海'}, 'expected_revision': 0}
    assert c.put(url, json=body).json()['data']['revision'] == 1
    assert c.put(url, json=body).status_code == 409
    assert c.get(url + '/history').json()['data'][0]['revision'] == 1
    assert c.get('/projects/p/studios/intro/missing').status_code == 404
    assert c.get('/projects/foreign/studios/intro').status_code == 403
    assert c.get('/projects/p/studios/unknown').status_code == 422
    user['role'] = 'viewer'
    assert c.put(url, json={**body, 'expected_revision': 1}).status_code == 403
    assert c.get(url).status_code == 200


def test_api_rejects_empty_title_and_nonfinite_data(client):
    c, _ = client
    assert c.put('/projects/p/studios/intro/title', json={'name': ' ', 'data': {}, 'expected_revision': 0}).status_code == 422
