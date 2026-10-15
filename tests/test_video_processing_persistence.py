from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from novelvideo.freezone.video_processing_projection import ProcessingRequests, prepare_processing_candidate, project_processing_result
from novelvideo.freezone.orchestration.projection import create_canvas, patch_graph
from novelvideo.freezone.canvas_store import read_canvas, save_canvas
from novelvideo.freezone.video_processing import fingerprint


def seed(root):
    source = root / 'source.mp4'
    source.write_bytes(b'original')
    create_canvas(root, 'board', project_id='p', name='board', actor_id='alice')
    patch_graph(root, 'board', base_revision=1, request_id='seed', operations=[{'op': 'add_node',
        'node': {'id': 'video', 'type': 'videoNode', 'data': {'videoUrl': '/source.mp4'}, 'position': {'x': 0, 'y': 0}}}])
    return {'source_url': '/source.mp4', 'source_fingerprint': fingerprint(source), 'node_id': 'video', 'canvas_id': 'board', 'operation': 'extend', 'request_id': 'request'}


def test_registry_claim_is_atomic_and_changed_parameters_conflict(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    registry = ProcessingRequests(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: registry.claim('alice', 'one', 'signature'), range(2)))
    assert sum(value is not None for value in values) == 1
    with pytest.raises(ValueError):
        registry.lookup('alice', 'one', 'different')
    registry.complete('alice', 'one', {'data': {'job_id': values[0] or values[1]}})
    assert registry.lookup('alice', 'one', 'signature')['response']['data']['job_id']
    assert registry.lookup('bob', 'one', 'signature') is None


@pytest.mark.parametrize('delete_source', [False, True])
def test_server_projection_is_stable_and_keeps_original(tmp_path, delete_source):
    payload = seed(tmp_path)
    result_id = prepare_processing_candidate(tmp_path, 'job', payload)
    prepare_processing_candidate(tmp_path, 'job', payload)
    assert len(read_canvas(tmp_path, 'board')['nodes']) == 2
    if delete_source:
        def remove(existing):
            result = deepcopy(existing)
            result['nodes'] = [n for n in result['nodes'] if n['id'] != 'video']
            result['revision'] += 1
            return result
        save_canvas(tmp_path, 'board', base_revision=None, enforce_revision=False, build_payload=remove)
    for _ in range(2):
        response = project_processing_result(tmp_path, 'job', payload=payload, output_url='/new.mp4', meta={'operation': 'extend'})
        assert response['node_id'] == result_id
    saved = read_canvas(tmp_path, 'board')
    assert len(saved['nodes']) == (1 if delete_source else 2)
    result = next(n for n in saved['nodes'] if n['id'] == result_id)
    assert result['data']['videoUrl'] == '/new.mp4'
    if delete_source:
        assert result['data']['orchestration']['status'] == 'source_changed'
        assert saved['edges'] == []
    else:
        assert saved['nodes'][0]['data']['videoUrl'] == '/source.mp4'
    assert (tmp_path / 'source.mp4').read_bytes() == b'original'


def test_candidate_deleted_keeps_successful_artifact_recoverable(tmp_path):
    payload = seed(tmp_path)
    result = project_processing_result(tmp_path, 'job', payload=payload, output_url='/result.mp4', meta={})
    assert result['status'] == 'source_changed'
    assert '任务中心' in result['message']


def test_candidate_retry_preserves_frozen_config_before_request_claim(tmp_path):
    payload = seed(tmp_path)
    payload.update(request_signature='same-user-request', model_config_snapshot={'model': 'first'})
    prepare_processing_candidate(tmp_path, 'job', payload)
    retry = deepcopy(payload)
    retry['model_config_snapshot'] = {'model': 'changed'}
    prepare_processing_candidate(tmp_path, 'job', retry)
    assert retry['model_config_snapshot'] == {'model': 'first'}
    assert len(read_canvas(tmp_path, 'board')['nodes']) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('separate_roots', [False, True])
async def test_api_replay_after_response_loss_does_not_enqueue_twice(monkeypatch, tmp_path, separate_roots):
    from novelvideo.api.routes import freezone_video_processing as route, freezone
    from novelvideo.api.schemas import FreezoneVideoProcessingRequest
    from novelvideo.freezone import jobs
    payload = seed(tmp_path)
    state_root = tmp_path
    if separate_roots:
        state_root = tmp_path / 'team-state'
        state_root.mkdir()
        create_canvas(state_root, 'board', project_id='p', name='board', actor_id='alice')
        patch_graph(state_root, 'board', base_revision=1, request_id='seed', operations=[{'op': 'add_node',
            'node': {'id': 'video', 'type': 'videoNode', 'data': {'videoUrl': '/source.mp4'}, 'position': {'x': 0, 'y': 0}}}])
    calls = []
    async def resolve(*args):
        return SimpleNamespace(project_id='p', state_dir=state_root), 'alice', 'p', tmp_path, None
    async def probe(*args):
        return 4.0
    async def size(*args):
        return 1920, 1080
    async def enqueue(**kwargs):
        calls.append(kwargs)
        return {'ok': True, 'data': {'job_id': kwargs['job_id'], 'task_key': 'task', 'task_type': 'freezone_video_upscale'}}
    monkeypatch.setattr(freezone, '_resolve_freezone_project', resolve)
    monkeypatch.setattr(freezone, '_enqueue_or_start_freezone_media_job', enqueue)
    monkeypatch.setattr(route, 'require_capability', lambda *args: SimpleNamespace(max_input_seconds=0))
    monkeypatch.setattr(jobs, '_probe_video_duration', probe)
    monkeypatch.setattr(jobs, '_probe_video_size', size)
    body = FreezoneVideoProcessingRequest(**payload)
    first = await route.process_video('p', body, {'username': 'alice'})
    # Simulate a lost response followed by source mutation: replay returns its existing task.
    (tmp_path / 'source.mp4').write_bytes(b'changed')
    second = await route.process_video('p', body, {'username': 'alice'})
    assert first == second
    assert len(calls) == 1
    assert len(read_canvas(state_root, 'board')['nodes']) == 2
    processing = calls[0]['payload']['processing']
    assert processing['canvas_state_root'] == str(state_root)
    projection = project_processing_result(tmp_path, first['data']['job_id'], payload=processing, output_url='/result.mp4', meta={})
    assert projection['status'] == 'placed'
    assert read_canvas(state_root, 'board')['nodes'][1]['data']['videoUrl'] == '/result.mp4'
    if separate_roots:
        assert len(read_canvas(tmp_path, 'board')['nodes']) == 1
    with pytest.raises(HTTPException) as error:
        await route.process_video('p', body.model_copy(update={'prompt': 'different'}), {'username': 'alice'})
    assert error.value.status_code == 409
    # While the first submission is in flight, a duplicate must not enqueue again.
    import asyncio
    from novelvideo import task_state
    (tmp_path / 'source.mp4').write_bytes(b'original')
    entered, release = asyncio.Event(), asyncio.Event()
    async def slow_enqueue(**kwargs):
        calls.append(kwargs)
        entered.set()
        await release.wait()
        return {'ok': True, 'data': {'job_id': kwargs['job_id'], 'task_key': 'parallel'}}
    monkeypatch.setattr(freezone, '_enqueue_or_start_freezone_media_job', slow_enqueue)
    monkeypatch.setattr(task_state, 'get_task_manager', lambda: SimpleNamespace(get_task_for_project=lambda *a, **k: None))
    parallel = body.model_copy(update={'request_id': 'parallel'})
    pending = asyncio.create_task(route.process_video('p', parallel, {'username': 'alice'}))
    await asyncio.wait_for(entered.wait(), 2)
    try:
        with pytest.raises(HTTPException) as busy:
            await route.process_video('p', parallel, {'username': 'alice'})
        assert busy.value.status_code == 409
        assert len(calls) == 2
    finally:
        release.set()
        await pending
