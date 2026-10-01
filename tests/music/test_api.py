from types import SimpleNamespace
import io
import math
import struct
import wave

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def wav_bytes():
    data = io.BytesIO()
    with wave.open(data, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(b''.join(struct.pack('<h', int(1000*math.sin(i/8))) for i in range(8000)))
    return data.getvalue()


@pytest.fixture
def api(tmp_path, monkeypatch):
    from novelvideo.api.routes import music_desk as routes
    from novelvideo.music.store import MusicStore
    store = MusicStore(tmp_path/'music.db')
    monkeypatch.setattr(routes, 'get_store', lambda: store)
    monkeypatch.setattr(routes, 'media_root', lambda: tmp_path/'media')
    async def resolve(*a, **kw):
        return SimpleNamespace(project_dir=tmp_path, ctx=SimpleNamespace(project_id='p', output_dir=tmp_path))
    monkeypatch.setattr(routes, 'resolve_project_scope', resolve)
    app = FastAPI(); app.include_router(routes.router)
    app.dependency_overrides[routes.get_api_user] = lambda: {'id':'alice'}
    return TestClient(app), store, routes, app


def test_upload_list_range_and_other_user(api):
    client, store, routes, app = api
    result = client.post('/music-library/assets', files={'file': ('rain.wav', wav_bytes(), 'audio/wav')})
    assert result.status_code == 200, result.text
    asset = result.json()['data']
    assert client.get('/music-library/assets').json()['data'][0]['name'] == 'rain'
    url = '/music-library/versions/'+asset['versionId']+'/audio'
    assert client.get(url, headers={'Range':'bytes=0-9'}).status_code == 206
    app.dependency_overrides[routes.get_api_user] = lambda: {'id':'bob'}
    assert client.get(url).status_code == 403
    assert client.get('/music-library/assets').json()['data'] == []


def test_bad_upload_and_agent_library_block(api):
    client, store, routes, app = api
    assert client.post('/music-library/assets', files={'file':('bad.wav', b'bad')}).status_code == 422
    app.dependency_overrides[routes.get_api_user] = lambda: {'id':'alice', 'credential_kind':'agent_session', 'scopes':['projects:write']}
    assert client.get('/music-library/assets').status_code == 403


def test_matches_never_generate_and_project_reference(api):
    client, store, routes, app = api
    a = client.post('/music-library/assets', files={'file': ('rain.wav', wav_bytes())}).json()['data']
    out = client.post('/projects/p/music/favorites', json={'versionId':a['versionId']})
    assert out.status_code == 200, out.text
    matches = client.post('/projects/p/music/matches', json={'query':'rain'}).json()['data']
    assert matches[0]['versionId'] == a['versionId']
    assert store.jobs('p') == []
    app.dependency_overrides[routes.get_api_user] = lambda: {'id':'bob'}
    assert client.get('/projects/p/music/versions/'+a['versionId']+'/media').status_code == 200


def test_generate_requires_scope(api):
    client, store, routes, app = api
    app.dependency_overrides[routes.get_api_user] = lambda: {'id':'agent', 'scopes':['projects:write']}
    assert client.post('/projects/p/music/generation-jobs', json={'requestId':'a'*32, 'request':{'tags':'piano'}}).status_code == 403


def test_resume_only_claims_paused_known_remote_once(api, monkeypatch):
    client, store, routes, app = api
    calls = []
    async def enqueue(scope, db, project, job, created):
        calls.append(job['id'])
        return {'ok':True, 'data':routes.public_job(db.job(project, job['id']), project)}
    monkeypatch.setattr(routes, 'enqueue', enqueue)
    store.create_job('p','j','generate',{})
    assert client.post('/projects/p/music/jobs/j/resume').status_code == 422
    store.update_job('p','j',status='paused',remoteTaskId='remote')
    assert client.post('/projects/p/music/jobs/j/resume').status_code == 200
    assert client.post('/projects/p/music/jobs/j/resume').status_code == 409
    assert calls == ['j']
