import json
import subprocess

import pytest
from PIL import Image


def test_background_submit_requires_tasks_submit_scope(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from novelvideo.api.routes import studio_intro

    async def resolve(*args, **kwargs):
        return SimpleNamespace(project_dir=tmp_path)

    monkeypatch.setattr(studio_intro, 'resolve_project_scope', resolve)
    monkeypatch.setattr(studio_intro, 'list_image_models', lambda *args: [])
    app = FastAPI()
    app.include_router(studio_intro.router)
    app.dependency_overrides[studio_intro.get_api_user] = lambda: {'id': 'agent', 'scopes': ['projects:write']}
    app.dependency_overrides[studio_intro.get_media_capability_store] = lambda: None
    app.dependency_overrides[studio_intro.get_media_credential_resolver] = lambda: None
    response = TestClient(app).post('/projects/test/studios/intro-tools/background-jobs', json={
        'request_id': 'c' * 32, 'prompt': 'background', 'model': 'test-model',
    })
    assert response.status_code == 403
    assert 'tasks:submit' in response.json()['detail']
    assert not (tmp_path / 'studios').exists()


def test_intro_templates_are_distinct_and_title_is_separate(tmp_path):
    from novelvideo.creative_studios.intro import IntroSpec, render_frame, fonts
    background = Image.new('RGB', (320, 180), '#142238')
    font = fonts()[0]['id']
    pixels = []
    for effect in ['static', 'fade', 'typewriter', 'zoom', 'shine']:
        spec = IntroSpec(title='片头 Test', subtitle='Subtitle', font=font, effect=effect)
        pixels.append(b''.join(render_frame(background, spec, t).tobytes() for t in [.35, 1.2, 2.]))
    assert len(set(pixels)) == 5
    assert background.getpixel((160, 90)) == (20, 34, 56)


def test_intro_rejects_unsafe_paths_and_invalid_duration(tmp_path):
    from novelvideo.creative_studios.intro import IntroSpec, project_file
    with pytest.raises(ValueError):
        project_file(tmp_path, '../secret')
    with pytest.raises(ValueError):
        IntroSpec(duration=1000)


def test_real_intro_export_and_audio_preserving_insert(tmp_path):
    from novelvideo.creative_studios.intro import IntroSpec, render_video, fonts
    background = tmp_path / 'background.png'
    Image.new('RGB', (320, 180), '#142238').save(background)
    original = tmp_path / 'episode.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=320x180:r=24:d=1', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(original)], check=True)
    before = original.read_bytes()
    output = tmp_path / 'intro.mp4'
    spec = IntroSpec(title='测试', duration=1, font=fonts()[0]['id'], background='background.png', insert_video='episode.mp4')
    render_video(tmp_path, spec, output, lambda _: None, width=320)
    info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(output)]))
    assert float(info['format']['duration']) >= 1.9
    assert any(s['codec_type'] == 'audio' for s in info['streams'])
    assert original.read_bytes() == before
    def packets(path):
        return subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:a:0', '-c:a', 'copy', '-f', 'adts', 'pipe:1'])
    assert packets(original) == packets(output)


def test_intro_routes_upload_validate_frame_and_idempotency(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from novelvideo.api.routes import studio_intro
    from novelvideo.project_context import ProjectContext
    from novelvideo.task_state import get_task_manager
    import io
    ctx = ProjectContext(project_id='test', project_name='test', owner_type='user', owner_id='u', owner_username='tester', requester_user_id='u', requester_username='tester', requester_principals=(), effective_role='editor', home_node_id='local', output_dir=tmp_path, state_dir=tmp_path / '.state', runtime_dir=tmp_path / '.runtime', is_home_node=True)
    async def resolve(*args, **kwargs):
        return SimpleNamespace(project_dir=tmp_path, state_dir=tmp_path / '.state', ctx=ctx)
    monkeypatch.setattr(studio_intro, 'resolve_project_scope', resolve)
    app = FastAPI()
    app.include_router(studio_intro.router)
    app.dependency_overrides[studio_intro.get_api_user] = lambda: {'id': 'test'}
    client = TestClient(app)
    prefix = '/projects/test/studios/intro-tools'
    assert client.post(prefix + '/upload', files={'file': ('bad.png', b'not an image')}).status_code == 422
    buf = io.BytesIO()
    Image.new('RGB', (32, 32)).save(buf, format='PNG')
    uploaded = client.post(prefix + '/upload', files={'file': ('test.png', buf.getvalue())}).json()['data']
    assert (tmp_path / uploaded['path']).is_file()
    caps = client.get(prefix + '/capabilities').json()['data']
    spec = {'background': uploaded['path'], 'font': caps['fonts'][0]['id'], 'duration': 1}
    assert client.post(prefix + '/preview', json={'spec': spec, 'time': .5}).headers['content-type'] == 'image/png'
    assert client.post(prefix + '/frame', json={'video': '../escape.mp4', 'time': 0}).status_code == 422
    original_launch = studio_intro._launch
    monkeypatch.setattr(studio_intro, '_launch', lambda *args: None)
    body = {'request_id': 'a' * 32, 'spec': spec}
    a = client.post(prefix + '/jobs', json=body).json()['data']
    b = client.post(prefix + '/jobs', json=body).json()['data']
    assert a['id'] == b['id']
    task = get_task_manager().get_task_for_project(ctx, 'studio_intro_render', 0, scope=a['id'])
    assert task is not None
    assert a['task_id'] == task.task_id
    changed = {**body, 'spec': {**spec, 'title': 'Changed'}}
    assert client.post(prefix + '/jobs', json=changed).status_code == 409
    monkeypatch.setattr(studio_intro, '_launch', original_launch)
    rendered = client.post(prefix + '/jobs', json={**body, 'request_id': 'b' * 32}).json()['data']
    import time
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        rendered = next(job for job in client.get(prefix + '/jobs').json()['data'] if job['id'] == rendered['id'])
        if rendered['status'] in ('completed', 'failed'):
            break
        time.sleep(.1)
    assert rendered['status'] == 'completed', rendered
    assert (tmp_path / rendered['output']).is_file()
    assert get_task_manager().get_task_for_project(ctx, 'studio_intro_render', 0, scope=rendered['id']).status == 'completed'
    from novelvideo.production_workflow import ProductionWorkflowStore
    _, versions = ProductionWorkflowStore(ctx.state_dir / 'production_workflow.json').get_slot('studio:intro:output')
    assert versions[rendered['id']].asset_path == rendered['output']


def test_interrupted_intro_task_is_recoverable(tmp_path):
    from novelvideo.api.routes.studio_intro import _read
    path = tmp_path / 'job.json'
    path.write_text(json.dumps({'id':'interrupted', 'status':'running', 'pid':0, 'spec':{'title':'Keep title'}}))
    job = _read(path)
    assert job['status'] == 'failed'
    assert job['spec']['title'] == 'Keep title'
    assert '中断' in job['error']


@pytest.mark.asyncio
async def test_ai_background_uses_existing_queue_once_and_returns_project_image(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.api.routes import studio_intro
    from novelvideo.project_context import ProjectContext
    from novelvideo.task_state import get_task_manager
    ctx = ProjectContext(project_id='ai-test', project_name='test', owner_type='user', owner_id='u', owner_username='tester', requester_user_id='u', requester_username='tester', requester_principals=(), effective_role='editor', home_node_id='local', output_dir=tmp_path, state_dir=tmp_path / '.state', runtime_dir=tmp_path / '.runtime', is_home_node=True)
    async def resolve(*args, **kwargs):
        return SimpleNamespace(project_dir=tmp_path, ctx=ctx)
    calls = []
    async def enqueue(context, **kwargs):
        calls.append(kwargs)
        state = get_task_manager().create_task_for_project(context, 'freezone_gen', 0, scope=kwargs['scope'])
        return SimpleNamespace(task_state=state)
    monkeypatch.setattr(studio_intro, 'resolve_project_scope', resolve)
    monkeypatch.setattr(studio_intro, 'list_image_models', lambda *_: [SimpleNamespace(id='test-model', label='Test', provider='test-provider', provider_id='test-account')])
    monkeypatch.setattr(studio_intro, 'get_task_backend', lambda: SimpleNamespace(enqueue_project_task=enqueue))
    body = studio_intro.BackgroundRequest(request_id='c' * 32, prompt='山海之间，无文字背景', model='test-model')
    a = await studio_intro.submit_background('ai-test', body, {}, None, None)
    b = await studio_intro.submit_background('ai-test', body, {}, None, None)
    assert a['data']['id'] == b['data']['id']
    assert len(calls) == 1
    assert calls[0]['task_type'] == 'freezone_gen'
    assert calls[0]['payload']['prompt'] == body.prompt
    path = tmp_path / 'background.png'
    Image.new('RGB', (64, 64)).save(path)
    get_task_manager().complete_task_for_project(ctx, 'freezone_gen', 0, scope=body.request_id, result={'output_path': str(path)})
    result = await studio_intro.background_jobs('ai-test', {})
    assert result['data'][0]['output'] == 'background.png'
    assert result['data'][0]['status'] == 'completed'
