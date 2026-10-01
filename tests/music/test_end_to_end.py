import subprocess


def test_library_plan_and_frozen_render_round_trip(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from novelvideo.api.routes import music_desk as routes
    from novelvideo.task_backend.runners import music as runner
    from novelvideo.music.store import MusicStore
    from novelvideo.music.media import digest
    from test_api import wav_bytes

    original=tmp_path/'episode.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=blue:s=160x90:r=24:d=1','-c:v','libx264',str(original)],check=True)
    before=digest(original)
    store=MusicStore(tmp_path/'music.db')
    monkeypatch.setattr(routes,'get_store',lambda:store)
    monkeypatch.setattr(routes,'media_root',lambda:tmp_path/'media')
    monkeypatch.setattr(runner,'get_store',lambda:store)
    monkeypatch.setattr(runner,'media_root',lambda:tmp_path/'media')
    async def resolve(*a,**kw):return SimpleNamespace(project_dir=tmp_path,ctx=SimpleNamespace(project_id='p'))
    monkeypatch.setattr(routes,'resolve_project_scope',resolve)
    app=FastAPI();app.include_router(routes.router)
    app.dependency_overrides[routes.get_api_user]=lambda:{'id':'alice'}
    client=TestClient(app)
    a=client.post('/music-library/assets',files={'file':('music.wav',wav_bytes())}).json()['data']
    source=client.post('/projects/p/music/sources',json={'url':'episode.mp4'}).json()['data']
    plan=dict(schemaVersion=1,revision=0,source=dict(assetVersionId=source['versionId'],sha256=source['sha256'],durationMs=source['durationMs']),tracks=[dict(id='t1',name='music',clips=[dict(id='c1',assetVersionId=a['versionId'],startMs=0,sourceInMs=0,lengthMs=1000)])])
    saved=client.put('/projects/p/music/plans/c/n',json=plan)
    assert saved.status_code==200,saved.text
    frozen=saved.json()['data']
    assert client.put('/projects/p/music/plans/c/n',json=plan).status_code==409
    assert client.get('/projects/p/music/plans/c/n').json()['data']['assets'][0]['name']=='music'
    store.create_job('p','f'*32,'render',{'plan':frozen,'preview':False})
    result=runner.run_music_render({'payload':{'job_id':'f'*32}},SimpleNamespace(project_id='p'))
    assert result['output_path'].endswith('scored.mp4')
    assert client.get('/projects/p/music/jobs/'+'f'*32+'/video').status_code==200
    assert digest(original)==before


def test_cancel_check_terminates_ffmpeg(tmp_path):
    import pytest
    from novelvideo.music.mix import run
    def stop(): raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError,match='cancelled'):
        run(['-f','lavfi','-i','anullsrc','-f','null','-'],check_cancel=stop)
