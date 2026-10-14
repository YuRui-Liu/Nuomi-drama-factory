"""Real viewer writes share the scene publication boundary and fixed history."""
import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from novelvideo.api.routes import scenes
from novelvideo.api.schemas import PanoViewerCorrection
from novelvideo.creation.asset_adapter import AssetAdapter
from novelvideo.creation.store import ObjectStore
from novelvideo.director_world import stage_manifest
from .test_scene_media_publication import project


async def call_writer(kind, name='Hall'):
    if kind == 'correction':
        return await scenes.update_scene_pano_correction('test', name, PanoViewerCorrection(front_yaw_deg=42), {})
    body = {'snapshot': {'camera': {'x': 7}, 'world': {'activeSourceId': 'new'}},
            'active_source_id': 'new', 'source_id': 'new'}
    return await getattr(scenes, {'world': 'save_scene_director_world', 'source': 'save_scene_director_world_source',
                                 'clear': 'clear_scene_director_world'}[kind])('test', name, body if kind != 'clear' else {'active_source_id': 'old'}, {})


@pytest.fixture
async def viewer(project, monkeypatch):
    source, ctx = project
    for name in ['Hall', 'Night']:
        directory = stage_manifest.stage_dir(ctx.output_dir, name)
        directory.mkdir(parents=True)
        (directory / 'pano.png').write_bytes(b'fixed panorama')
        (directory / 'custom.sog').write_bytes(b'fixed model')
        stage_manifest.update_manifest(ctx.output_dir, name, pano_path='pano.png', custom_scene_path='custom.sog')
        stage_manifest.save_scene_director_world(ctx.output_dir, name, active_source_id='old', snapshot={'camera': {'x': 1}})
    result = SimpleNamespace(model_dump=lambda **kwargs: {'viewer': 'scene'})
    monkeypatch.setattr(scenes, 'build_director_stage_manifest', lambda **kwargs: result)
    monkeypatch.setattr(scenes, 'build_pano_viewer_manifest', lambda **kwargs: result)
    return source, ctx


@pytest.mark.parametrize('kind', ['correction', 'world', 'source', 'clear'])
@pytest.mark.parametrize('registered', [True, False])
@pytest.mark.parametrize('name', ['Hall', 'Night'])
async def test_viewer_routes_publish_fixed_manifest_and_preserve_history(viewer, kind, registered, name):
    source, ctx = viewer
    if registered:
        adapter = AssetAdapter(source, ObjectStore(source.state_dir))
        base = await adapter.register_base('scene', 'Hall')
        ref = base if name == 'Hall' else await adapter.register_variant(base.base_asset_id, 'Night')
        oid = ref.variant_id or ref.base_asset_id
        before = adapter.objects.read(oid)
    assert (await call_writer(kind, name))['ok']
    if registered:
        after = adapter.objects.read(oid)
        assert after['revision'] == before['revision'] + 1
        assert after['payload']['_scene_manifest'] != before['payload']['_scene_manifest']
        assert adapter.objects.read(oid, before['revision']) == before
        for media in before['payload']['_media']:
            assert (ctx.output_dir / media['current_blob']['path']).read_bytes() in {b'fixed panorama', b'fixed model'}
        # Replaying an identical view edit must not invent another content version.
        if kind != 'clear':
            await call_writer(kind, name)
            assert adapter.objects.read(oid)['revision'] == after['revision']
    else:
        assert not (Path(ctx.state_dir) / 'creation').exists()


@pytest.mark.parametrize('cancel', [False, True])
@pytest.mark.parametrize('phase', ['published', 'captured'])
async def test_viewer_failure_preserves_pending_and_fixed_history(viewer, monkeypatch, cancel, phase):
    from novelvideo.creation import asset_media_publication as publication
    from novelvideo.creation.asset_sync import SourceSyncPending
    source, ctx = viewer
    adapter = AssetAdapter(source, ObjectStore(source.state_dir))
    ref = await adapter.register_base('scene', 'Hall')
    before = adapter.objects.read(ref.base_asset_id)
    def checkpoint(at):
        if at == phase:
            raise asyncio.CancelledError() if cancel else RuntimeError('interrupted')
    monkeypatch.setattr(publication, '_checkpoint', checkpoint)
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        await call_writer('source')
    monkeypatch.setattr(publication, '_checkpoint', lambda _: None)
    with pytest.raises(HTTPException) as error:
        await call_writer('correction')
    assert error.value.status_code == 409
    assert error.value.detail['code'] == 'asset_media_pending'
    assert adapter.objects.read(ref.base_asset_id, before['revision']) == before
    service = publication.MediaPublication(publication.SourceSync(source, ObjectStore(source.state_dir)))
    token = next(iter(service.entries().values()))['token']
    if phase == 'published':
        with pytest.raises(SourceSyncPending):
            await service.reconcile(token)
    else:
        await service.reconcile(token)
        assert not service.entries()
        assert adapter.objects.read(ref.base_asset_id)['revision'] == 2


async def test_manifest_source_read_modify_write_does_not_lose_parallel_sources(tmp_path, monkeypatch):
    original = stage_manifest.get_scene_director_world
    first_read, release = threading.Event(), threading.Event()
    counter_lock = threading.Lock()
    reads = 0
    def read(*args, **kwargs):
        nonlocal reads
        value = original(*args, **kwargs)
        with counter_lock:
            reads += 1
            first = reads == 1
        if first:
            first_read.set()
            assert release.wait(5)
        return value
    monkeypatch.setattr(stage_manifest, 'get_scene_director_world', read)
    def save(source):
        return stage_manifest.save_scene_director_world_source(tmp_path, 'Hall', source_id=source, snapshot={'camera': {'x': source}})
    first = asyncio.create_task(asyncio.to_thread(save, 'one'))
    assert await asyncio.to_thread(first_read.wait, 5)
    second = asyncio.create_task(asyncio.to_thread(save, 'two'))
    await asyncio.sleep(.1)
    release.set()
    await asyncio.gather(first, second)
    assert set(original(tmp_path, 'Hall')['scenes_by_source_id']) == {'one', 'two'}


@pytest.mark.parametrize('kind', ['correction', 'world', 'source', 'clear'])
async def test_all_viewer_writes_require_editor_before_touching_files(viewer, monkeypatch, kind):
    source, ctx = viewer
    path = stage_manifest.manifest_path(ctx.output_dir, 'Hall')
    before = path.read_bytes()
    async def denied(project, user, *, required_role):
        assert required_role == 'editor'
        raise HTTPException(403, 'editor required')
    monkeypatch.setattr(scenes, '_resolve_scene_project', denied)
    with pytest.raises(HTTPException) as error:
        await call_writer(kind)
    assert error.value.status_code == 403
    assert path.read_bytes() == before
    assert not (Path(ctx.state_dir) / 'creation').exists()


@pytest.mark.parametrize('kind', ['correction', 'world', 'source'])
async def test_invalid_viewer_request_does_not_publish_or_leave_journal(viewer, monkeypatch, kind):
    from novelvideo.creation import asset_media_publication as publication
    source, ctx = viewer
    adapter = AssetAdapter(source, ObjectStore(source.state_dir))
    ref = await adapter.register_base('scene', 'Hall')
    before = adapter.objects.read(ref.base_asset_id)
    path = stage_manifest.manifest_path(ctx.output_dir, 'Hall')
    manifest_before = path.read_bytes()
    if kind == 'correction':
        monkeypatch.setattr(scenes, 'build_pano_viewer_manifest', lambda **kwargs: None)
        result = await call_writer(kind)
    else:
        method = scenes.save_scene_director_world if kind == 'world' else scenes.save_scene_director_world_source
        result = await method('test', 'Hall', {'snapshot': {}}, {})
    assert result['ok'] is False
    assert path.read_bytes() == manifest_before
    assert adapter.objects.read(ref.base_asset_id) == before
    service = publication.MediaPublication(publication.SourceSync(source, adapter.objects))
    assert not service.entries()


async def test_waiting_viewer_writer_rereads_sql_and_freezes_request_body(viewer, monkeypatch):
    from novelvideo.production_workflow.coordination import asset_project_lock
    source, ctx = viewer
    adapter = AssetAdapter(source, ObjectStore(source.state_dir))
    ref = await adapter.register_base('scene', 'Hall')
    body = {'source_id': 'new', 'snapshot': {'camera': {'x': 4}}}
    resolved = asyncio.Event()
    original = scenes._resolve_scene_project
    async def resolve(*args, **kwargs):
        result = await original(*args, **kwargs)
        resolved.set()
        return result
    monkeypatch.setattr(scenes, '_resolve_scene_project', resolve)
    async with asset_project_lock(ctx.state_dir):
        task = asyncio.create_task(scenes.save_scene_director_world_source('test', 'Hall', body, {}))
        await resolved.wait()
        body['snapshot']['camera']['x'] = 999
        await source.update_scene('Hall', environment_prompt='fresh input')
    assert (await task)['ok']
    current = adapter.objects.read(ref.base_asset_id)
    assert current['payload']['environment_prompt'] == 'fresh input'
    assert current['payload']['_scene_manifest']['scenes_by_source_id']['new']['camera']['x'] == 4


async def test_waiting_retired_name_is_rejected_without_writing_old_manifest(viewer, monkeypatch):
    from novelvideo.production_workflow.coordination import asset_project_lock
    source, ctx = viewer
    adapter = AssetAdapter(source, ObjectStore(source.state_dir))
    await adapter.register_base('scene', 'Hall')
    old_path = stage_manifest.manifest_path(ctx.output_dir, 'Hall')
    old_bytes = old_path.read_bytes()
    resolved = asyncio.Event()
    original = scenes._resolve_scene_project
    async def resolve(*args, **kwargs):
        result = await original(*args, **kwargs)
        resolved.set()
        return result
    monkeypatch.setattr(scenes, '_resolve_scene_project', resolve)
    async with asset_project_lock(ctx.state_dir):
        task = asyncio.create_task(call_writer('world'))
        await resolved.wait()
        await source.rename_scene('Hall', 'NewHall')
    with pytest.raises(HTTPException) as error:
        await task
    assert error.value.status_code == 409
    assert old_path.read_bytes() == old_bytes
    assert (await call_writer('world', 'NewHall'))['ok']


async def test_concurrent_viewer_routes_preserve_both_source_layouts(viewer):
    source, ctx = viewer
    adapter = AssetAdapter(source, ObjectStore(source.state_dir))
    ref = await adapter.register_base('scene', 'Hall')
    before = adapter.objects.read(ref.base_asset_id)
    results = await asyncio.gather(*(
        scenes.save_scene_director_world_source('test', 'Hall', {
            'source_id': name, 'snapshot': {'camera': {'x': number}},
        }, {}) for number, name in enumerate(['one', 'two'])
    ))
    assert all(result['ok'] for result in results)
    current = adapter.objects.read(ref.base_asset_id)
    assert current['revision'] == before['revision'] + 2
    layouts = current['payload']['_scene_manifest']['scenes_by_source_id']
    assert layouts['one']['camera']['x'] == 0
    assert layouts['two']['camera']['x'] == 1
    assert adapter.objects.read(ref.base_asset_id, before['revision']) == before


async def test_real_missing_panorama_does_not_write_correction(project):
    from novelvideo.creation import asset_media_publication as publication
    source, ctx = project
    adapter = AssetAdapter(source, ObjectStore(source.state_dir))
    ref = await adapter.register_base('scene', 'Hall')
    before = adapter.objects.read(ref.base_asset_id)
    assert not stage_manifest.manifest_path(ctx.output_dir, 'Hall').exists()
    result = await call_writer('correction')
    assert result['ok'] is False
    assert not stage_manifest.manifest_path(ctx.output_dir, 'Hall').exists()
    assert adapter.objects.read(ref.base_asset_id) == before
    assert not publication.MediaPublication(publication.SourceSync(source, adapter.objects)).entries()
