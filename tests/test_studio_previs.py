import pytest
from pydantic import ValidationError

from novelvideo.creative_studios.previs import PrevisScene


def scene():
    return dict(actors=[dict(id='a', name='演员', humanoid=True, position=[0, 0, 0], yaw=0)], clips=[], camera=[dict(id='c', time=0, position=[8, 6, 10], target=[0, 1, 0])], light=dict(yaw=30, intensity=1), props=[])


def test_previs_rejects_overlaps_and_incompatible_actor():
    data = scene()
    clip = dict(id='one', actorId='a', action='walk', start=0, duration=2, target=[4, 0, 0], yaw=0)
    data['clips'] = [clip, dict(clip, id='two', start=1)]
    with pytest.raises(ValidationError, match='重叠'):
        PrevisScene.model_validate(data)
    data['clips'] = [clip]
    data['actors'][0]['humanoid'] = False
    with pytest.raises(ValidationError, match='人形'):
        PrevisScene.model_validate(data)


def test_previs_accepts_valid_timeline_and_rejects_nan():
    assert PrevisScene.model_validate(scene()).camera[0].time == 0
    data = scene()
    data['camera'][0]['position'][0] = float('nan')
    with pytest.raises(ValidationError):
        PrevisScene.model_validate(data)


def test_previs_output_persists_verified_media_and_idempotent_manifest(tmp_path, monkeypatch):
    import io
    import json
    import subprocess
    from types import SimpleNamespace
    from PIL import Image
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from novelvideo.api.routes import studio_previs

    async def resolve(*args, **kwargs):
        return SimpleNamespace(project_dir=tmp_path)
    monkeypatch.setattr(studio_previs, 'resolve_project_scope', resolve)
    app = FastAPI()
    app.include_router(studio_previs.router)
    app.dependency_overrides[studio_previs.get_api_user] = lambda: {'id': 'test'}
    client = TestClient(app)
    url = '/projects/test/studios/previs-tools/outputs'
    manifest = json.dumps({'scene': scene(), 'revision': 2})
    assert client.post(url, data={'manifest': manifest}, files={'file': ('bad.webm', b'no-video')}).status_code == 422
    png = io.BytesIO()
    Image.new('RGB', (64, 64), 'white').save(png, format='PNG')
    for invalid in [-1, float('nan'), 4000]:
        assert client.post(url, data={'manifest': json.dumps({'scene': scene(), 'time': invalid})}, files={'file': ('frame.png', png.getvalue())}).status_code == 422
    first = client.post(url, data={'manifest': manifest}, files={'file': ('frame.png', png.getvalue())})
    assert first.status_code == 200, first.text
    second = client.post(url, data={'manifest': manifest}, files={'file': ('frame.png', png.getvalue())})
    assert first.json()['data']['id'] == second.json()['data']['id']
    assert len(client.get(url).json()['data']) == 1
    assert client.get(url + '/' + first.json()['data']['id'] + '/media').headers['content-type'] == 'image/png'
    video = tmp_path / 'test.webm'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=white:s=64x64:r=10:d=0.5', '-c:v', 'libvpx', str(video)], check=True)
    result = client.post(url, data={'manifest': manifest}, files={'file': ('previs.webm', video.read_bytes())})
    assert result.status_code == 200, result.text
    assert result.json()['data']['kind'] == 'video'
    assert client.get(url + '/' + result.json()['data']['id'] + '/media').content == video.read_bytes()
    (tmp_path / 'studios' / 'previs' / 'outputs' / 'broken.json').write_text('{')
    assert len(client.get(url).json()['data']) == 2
