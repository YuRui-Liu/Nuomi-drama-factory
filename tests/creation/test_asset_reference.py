from pathlib import Path

import pytest

from tests.creation.test_asset_media import media_project, candidate


def files(root):
    # Read-only SQLite transactions maintain shared reader locks and may create
    # an empty WAL. Keep all persistent SQL bytes (including nonempty WAL).
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()
            and not p.name.endswith('-shm') and not (p.name.endswith('-wal') and p.stat().st_size == 0)}


def test_empty_catalog_does_not_initialize(tmp_path):
    from novelvideo.creation.asset_reference import AssetReferences
    before = files(tmp_path)
    assert AssetReferences(tmp_path / 'state', tmp_path / 'output', 'p').catalog() == []
    assert files(tmp_path) == before


async def test_sql_asset_browse_never_bootstraps_creation(tmp_path):
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.models import NovelCharacter
    from novelvideo.creation.asset_reference import AssetReferences
    source = SQLiteStore('test', output_dir=str(tmp_path / 'output'), state_dir=str(tmp_path / 'state'))
    await source.initialize()
    await source.add_character(NovelCharacter(name='Existing'))
    await source.close()
    before = files(tmp_path)
    entries = AssetReferences(tmp_path / 'state', tmp_path / 'output', 'p').catalog()
    assert entries[0]['source_name'] == 'Existing' and entries[0]['asset_ref'] is None
    assert files(tmp_path) == before
    assert not (tmp_path / 'state' / 'creation').exists()


async def test_catalog_selection_is_readonly_and_registration_checks_proof(media_project):
    from novelvideo.creation.asset_reference import AssetReferences, AssetReferenceError
    source, adapter = media_project
    path = candidate(source, provisional=True, content=b'original')
    service = AssetReferences(source.state_dir, source.project_dir, 'p')
    before = files(Path(source.state_dir).parent)
    entry = next(e for e in service.catalog() if e['kind'] == 'character' and e['source_name'] == 'Lin' and not e['parent_source_name'])
    assert entry['asset_ref'] is None
    assert files(Path(source.state_dir).parent) == before
    path.write_bytes(b'changed')
    with pytest.raises(AssetReferenceError, match='变化'):
        await service.register(entry['selection'], entry['proof'], actor='alice')
    assert not adapter.objects._connect().execute('SELECT 1 FROM asset_sources').fetchone()


async def test_fixed_media_resolves_original_bytes_after_refresh(media_project):
    from novelvideo.creation.asset_reference import AssetReferences
    source, adapter = media_project
    path = candidate(source, provisional=True, content=b'original')
    ref = await adapter.register_base('character', 'Lin')
    service = AssetReferences(source.state_dir, source.project_dir, 'p')
    selected = {'slot_id': 'character:Lin:portrait', 'role': 'portrait'}
    first = service.materialize(ref, selected, 'uploadNode')
    path.write_bytes(b'changed')
    await adapter.refresh_source(ref.base_asset_id, expected_revision=1)
    assert service.materialize(ref, selected, 'uploadNode') == first
    assert first['data']['imageUrl'].startswith('/static/projects/p/assets/.creation-media/')
    assert (Path(source.project_dir) / first['blobs'][0]['path']).read_bytes() == b'original'


async def test_atomic_new_node_and_ack_retry(media_project):
    from novelvideo.creation.operations import OperationService
    from novelvideo.creation.models import OperationRequest
    from novelvideo.creation.projection import ProjectionService
    from novelvideo.freezone.orchestration.projection import create_canvas
    from novelvideo.freezone.canvas_store import read_canvas
    source, adapter = media_project
    candidate(source, provisional=True)
    ref = await adapter.register_base('character', 'Lin')
    create_canvas(Path(source.state_dir), 'canvas', project_id='p', name='test', actor_id='alice')
    with adapter.objects.transaction() as conn:
        conn.execute("INSERT INTO canvas_workspaces VALUES ('canvas', 'free', NULL, 'test', 'fixture', 'fixture')")
    service = OperationService(adapter.objects, project_id='p', output_dir=source.project_dir, authorize=lambda *_: True)
    request = OperationRequest(request_id='bind', kind='asset.bind', scope={'project_id': 'p', 'canvas_id': 'canvas', 'node_ids': ['asset']},
        expected_revisions={'canvas': 1}, payload={'node_id': 'asset', 'asset_ref': ref.model_dump(),
            'media': {'slot_id': 'character:Lin:portrait', 'role': 'portrait'},
            'create': {'type': 'uploadNode', 'position': {'x': 12, 'y': 34}}})
    pending = service.apply('alice', request)
    assert read_canvas(Path(source.state_dir), 'canvas')['nodes'] == []
    applied = ProjectionService(adapter.objects).apply(pending.operation_id)
    assert applied.status == 'applied'
    nodes = read_canvas(Path(source.state_dir), 'canvas')['nodes']
    assert len(nodes) == 1
    assert nodes[0]['data']['assetRef'] == ref.model_dump()
    assert '.creation-media/' in nodes[0]['data']['imageUrl']
    archive = adapter.objects.read(ref.base_asset_id)['payload']['_media'][0]['current_blob']['path']
    (Path(source.project_dir) / archive).unlink()
    assert service.apply('alice', request) == applied


def test_ordinary_save_cannot_forge_ref_even_with_projection_source(tmp_path):
    from novelvideo.freezone.canvas_store import save_canvas
    from novelvideo.freezone.orchestration.projection import create_canvas
    create_canvas(tmp_path, 'canvas', project_id='p', name='test', actor_id='alice')
    with pytest.raises(ValueError, match='asset_binding'):
        save_canvas(tmp_path, 'canvas', base_revision=1, save_source='creation_projection',
            build_payload=lambda old: {**old, 'nodes': [{'id': 'forged', 'type': 'uploadNode',
                'data': {'assetRef': {'base_asset_id': 'fake', 'revision': 1}, 'imageUrl': 'external'}}]})


async def test_scene_bundle_uses_only_fixed_archives(media_project):
    from novelvideo.creation.asset_reference import AssetReferences
    from novelvideo.director_world import stage_manifest
    source, adapter = media_project
    root = Path(source.project_dir)
    directory = stage_manifest.stage_dir(root, 'Hall')
    directory.mkdir(parents=True)
    for name in ('custom.sog', 'master.ply', 'depth.sog', 'collision.glb', 'voxels.json', 'pano.png'):
        (directory / name).write_bytes(name.encode())
    stage_manifest.update_manifest(root, 'Hall', custom_scene_path='custom.sog', master_ply_path='master.ply',
        collision_glb_path='collision.glb', voxel_json_path='voxels.json', pano_path='pano.png', pano_depth_ply_path='depth.sog',
        pano_correction={'yaw_deg': 42})
    ref = await adapter.register_base('scene', 'Hall')
    result = AssetReferences(source.state_dir, root, 'p').materialize(ref,
        {'slot_id': 'scene:Hall:base:custom_scene', 'role': 'custom_scene'}, 'threeDWorldNode')['data']
    assert all(s['source_type'] == ('pano360' if s['id'] == 'pano' else 'sog') for s in result['sources'])
    assert all(s['source_kind'] in {'active', 'master', 'reverse', 'pano', 'custom'} for s in result['sources'])
    assert result['assetSceneManifest']['pano_correction'] == {'yaw_deg': 42}
    assert '.creation-media/' in result['assetSceneManifest']['collision_glb_path']
    assert '.creation-media/' in result['assetSceneManifest']['voxel_json_path']
    assert '.creation-media/' in result['panoUrl']
    assert len(result['sources']) >= 2


@pytest.mark.parametrize('changed', ['imageUrl', 'plyUrl', 'sources'])
def test_replace_conflict_preserves_every_media_field(tmp_path, changed):
    from tests.creation.test_operations import setup_service, registered_asset, bind_payload, request
    from tests.creation.test_projection import move
    from novelvideo.creation.projection import ProjectionService
    from novelvideo.freezone.canvas_store import read_canvas
    store, service, episode = setup_service(tmp_path, 'uploadNode')
    asset = registered_asset(store)
    pending = service.apply('alice', request(episode, kind='asset.bind', payload=bind_payload(asset)))
    move(tmp_path, data={changed: 'human-new', 'prompt': 'keep'})
    before = read_canvas(tmp_path, 'canvas-1')
    assert ProjectionService(store).apply(pending.operation_id).status == 'conflict'
    assert read_canvas(tmp_path, 'canvas-1') == before
    assert service.asset_uses('alice', asset['id']) == []


def test_binding_layout_save_delete_and_missing_use(tmp_path):
    from copy import deepcopy
    from tests.creation.test_operations import setup_service, registered_asset, bind_payload, request
    from novelvideo.creation.projection import ProjectionService
    from novelvideo.creation.asset_reference import AssetReferences
    from novelvideo.freezone.canvas_store import read_canvas, save_canvas
    store, service, episode = setup_service(tmp_path, 'uploadNode')
    asset = registered_asset(store)
    pending = service.apply('alice', request(episode, kind='asset.bind', payload=bind_payload(asset)))
    references = AssetReferences(tmp_path, tmp_path, 'p')
    assert references.uses(asset['id'])[0]['status'] == 'pending_projection'
    assert ProjectionService(store).apply(pending.operation_id).status == 'applied'
    assert references.uses(asset['id'])[0]['status'] == 'applied'
    payload = deepcopy(read_canvas(tmp_path, 'canvas-1'))
    payload['nodes'][0]['position'] = {'x': 100, 'y': 50}
    payload['nodes'][0]['data']['prompt'] = 'human prompt'
    payload['revision'] = 4
    save_canvas(tmp_path, 'canvas-1', base_revision=3, build_payload=lambda _: payload)
    for field, value in [('imageUrl', 'external'), ('assetRef', None), ('assetMediaSelection', {})]:
        forged = deepcopy(payload)
        forged['nodes'][0]['data'][field] = value
        with pytest.raises(ValueError, match='asset_binding'):
            save_canvas(tmp_path, 'canvas-1', base_revision=4, build_payload=lambda _: forged)
    save_canvas(tmp_path, 'canvas-1', base_revision=4, allow_empty_overwrite=True, save_source='manual_clear',
        build_payload=lambda old: {**old, 'nodes': [], 'edges': [], 'revision': 5})
    assert references.uses(asset['id'])[0]['status'] == 'missing'
    with pytest.raises(ValueError, match='asset_binding'):
        save_canvas(tmp_path, 'canvas-1', base_revision=5, build_payload=lambda _: payload)


async def test_registration_success_and_drift_is_not_refresh(media_project):
    from novelvideo.creation.asset_reference import AssetReferences, AssetReferenceError
    source, adapter = media_project
    candidate(source, provisional=True)
    references = AssetReferences(source.state_dir, source.project_dir, 'p')
    entry = next(e for e in references.catalog() if e['source_name'] == 'Lin')
    ref = await references.register(entry['selection'], entry['proof'], actor='alice')
    assert ref.revision == 1
    again = await references.register(entry['selection'], entry['proof'], actor='alice')
    assert again == ref
    import sqlite3
    with sqlite3.connect(source.db_path) as conn:
        conn.execute("UPDATE characters SET description='external change' WHERE name='Lin'")
    fresh = next(e for e in references.catalog() if e['source_name'] == 'Lin')
    with pytest.raises(AssetReferenceError):
        await references.register(fresh['selection'], fresh['proof'], actor='alice')
    assert adapter.objects.read(ref.base_asset_id)['revision'] == 1


async def test_readonly_routes_and_mode_guard(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from novelvideo.api.routes import creation
    from fastapi import HTTPException
    roles = []
    async def resolve(**kwargs):
        roles.append(kwargs['required_role'])
        return SimpleNamespace(project_id='p', requester_user_id='alice', state_dir=tmp_path,
                               output_dir=tmp_path / 'output', is_home_node=True)
    monkeypatch.setattr(creation, 'resolve_project_context', resolve)
    (tmp_path / 'project_config.json').write_text(json.dumps({'creation_mode': 'agent_canvas_v1'}))
    before = files(tmp_path)
    assert (await creation.browse_assets('p', {'id': 'alice'}))['data'] == []
    assert (await creation.asset_uses('p', 'unknown', {'id': 'alice'}))['data'] == []
    with pytest.raises(HTTPException):
        await creation.asset_version('p', 'unknown', 1, user={'id': 'alice'})
    assert files(tmp_path) == before
    assert roles == ['viewer'] * 3
    (tmp_path / 'project_config.json').write_text('{}')
    before = files(tmp_path)
    with pytest.raises(HTTPException) as exc:
        await creation.browse_assets('p', {'id': 'alice'})
    assert exc.value.status_code == 409
    assert files(tmp_path) == before


def test_fixed_three_d_allows_view_changes_but_not_source_forgery():
    from copy import deepcopy
    from novelvideo.creation.asset_reference import protect_bindings
    data = {'assetRef': {'base_asset_id': 'base', 'revision': 1}, 'assetMediaSelection': {'role': 'custom_scene'},
            'sources': [{'id': 'custom_scene', 'source_type': 'sog', 'ply_url': '/fixed', 'collision_glb_url': '/collision'}],
            'activeSourceId': 'custom_scene', 'plyUrl': '/fixed', 'scene': None}
    old = {'nodes': [{'id': 'n', 'type': 'threeDWorldNode', 'data': data}]}
    changed = deepcopy(old)
    changed['nodes'][0]['data']['sources'][0]['transform'] = {'yawDeg': 25, 'scale': 1}
    changed['nodes'][0]['data']['scene'] = {'schemaVersion': 1, 'actors': [], 'props': [], 'stagings': [], 'camera': {'distance': 5}}
    protect_bindings(old, changed)
    for field in ('ply_url', 'collision_glb_url', 'url', 'pano_url', 'fs', 'voxel_json_url'):
        forged = deepcopy(changed)
        forged['nodes'][0]['data']['sources'][0][field] = '/forged'
        with pytest.raises(ValueError, match='asset_binding'):
            protect_bindings(old, forged)


def test_forged_save_source_does_not_forge_receipt(tmp_path):
    from novelvideo.freezone.canvas_store import save_canvas, read_canvas
    from novelvideo.freezone.orchestration.projection import create_canvas
    create_canvas(tmp_path, 'canvas', project_id='p', name='test', actor_id='alice')
    save_canvas(tmp_path, 'canvas', base_revision=1, save_source='creation_projection',
        build_payload=lambda old: {**old, 'metadata': {'creation_projection_receipts': {'fake': {'revision': 2}}}})
    assert 'creation_projection_receipts' not in read_canvas(tmp_path, 'canvas').get('metadata', {})


def test_archive_loss_after_prepare_is_conflict_without_canvas_change(tmp_path):
    from tests.creation.test_operations import setup_service, registered_asset, bind_payload, request
    from novelvideo.creation.projection import ProjectionService
    from novelvideo.freezone.canvas_store import read_canvas
    store, service, episode = setup_service(tmp_path, 'uploadNode')
    asset = registered_asset(store)
    pending = service.apply('alice', request(episode, kind='asset.bind', payload=bind_payload(asset)))
    before = read_canvas(tmp_path, 'canvas-1')
    blob = asset['payload']['_media'][0]['current_blob']
    (tmp_path / blob['path']).unlink()
    assert ProjectionService(store).apply(pending.operation_id).status == 'conflict'
    assert read_canvas(tmp_path, 'canvas-1') == before


@pytest.mark.parametrize('mutation', ['role', 'slot', 'url', 'type', 'missing'])
def test_invalid_media_never_writes_operation(tmp_path, mutation):
    from tests.creation.test_operations import setup_service, registered_asset, bind_payload, request
    from novelvideo.creation.operations import OperationError
    store, service, episode = setup_service(tmp_path, 'uploadNode')
    asset = registered_asset(store)
    payload = bind_payload(asset)
    if mutation == 'role':
        payload['media']['role'] = 'custom_scene'
    elif mutation == 'slot':
        payload['media']['slot_id'] = 'character:Other:portrait'
    elif mutation == 'url':
        payload['imageUrl'] = 'https://forged'
    elif mutation == 'type':
        payload['create'] = {'type': 'imageGenNode'}
    else:
        (tmp_path / asset['payload']['_media'][0]['current_blob']['path']).unlink()
    with pytest.raises(OperationError):
        service.apply('alice', request(episode, kind='asset.bind', payload=payload))
    with store.transaction() as conn:
        assert conn.execute('SELECT count(*) FROM operations').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM relations').fetchone()[0] == 0


async def test_alias_registration_rejects_ambiguity(media_project):
    from novelvideo.creation.asset_reference import AssetReferences, AssetReferenceError
    from novelvideo.models import NovelCharacter
    source, _ = media_project
    await source.update_character('Lin', aliases=['Alias'])
    service = AssetReferences(source.state_dir, source.project_dir, 'p')
    entry = next(e for e in service.catalog() if e['source_name'] == 'Lin')
    selection = {**entry['selection'], 'source_name': 'Alias'}
    ref = await service.register(selection, entry['proof'], actor='alice')
    assert service.resolve(ref)['object']['payload']['name'] == 'Lin'
    await source.add_character(NovelCharacter(name='Other', aliases=['Alias']))
    with pytest.raises(AssetReferenceError):
        await service.register(selection, entry['proof'], actor='alice')


async def test_real_canvas_put_returns_typed_conflict(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from novelvideo.api.routes import freezone
    from novelvideo.api.schemas import CanvasPayload
    from novelvideo.freezone.orchestration.projection import create_canvas
    from novelvideo.freezone.canvas_store import read_canvas
    create_canvas(tmp_path, 'canvas', project_id='p', name='test', actor_id='alice')
    async def resolve(*args, **kwargs):
        return SimpleNamespace(state_dir=tmp_path), 'alice', 'test', tmp_path, str(tmp_path)
    monkeypatch.setattr(freezone, '_resolve_freezone_project', resolve)
    original = read_canvas(tmp_path, 'canvas')
    body = CanvasPayload(base_revision=1, save_source='autosave', nodes=[
        {'id': 'n', 'type': 'uploadNode', 'data': {'assetRef': {'base_asset_id': 'fake', 'revision': 1}}}], edges=[])
    with pytest.raises(HTTPException) as exc:
        await freezone.put_canvas('p', 'canvas', body, {'id': 'alice'})
    assert exc.value.status_code == 409
    assert exc.value.detail['code'] == 'asset_binding_readonly'
    assert read_canvas(tmp_path, 'canvas') == original


async def test_registration_api_editor_and_real_variant(media_project, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.api.routes import creation
    from novelvideo.utils.path_resolver import canonical_identity_portrait_path
    from novelvideo.creation.asset_reference import AssetReferences
    source, _ = media_project
    path = canonical_identity_portrait_path(Path(source.project_dir), 'Lin', 'coat')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'identity portrait')
    (Path(source.state_dir) / 'project_config.json').write_text('{"creation_mode":"agent_canvas_v1"}')
    roles = []
    async def resolve(**kwargs):
        roles.append(kwargs['required_role'])
        return SimpleNamespace(state_dir=source.state_dir, output_dir=source.project_dir,
                               project_id='p', requester_user_id='alice', is_home_node=True)
    monkeypatch.setattr(creation, 'resolve_project_context', resolve)
    entry = next(e for e in (await creation.browse_assets('p', {'id': 'alice'}))['data'] if e['source_name'] == 'identity')
    response = await creation.register_asset('p', {'selection': entry['selection'], 'proof': entry['proof']}, {'id': 'forged'})
    ref = response['data']
    assert ref['variant_id']
    resolved = AssetReferences(source.state_dir, source.project_dir, 'p').resolve(ref)
    assert resolved['object']['actor_id'] == 'alice'
    assert resolved['mapping']['parent_id'] == ref['base_asset_id']
    assert roles == ['viewer', 'editor']


async def test_model_with_canonical_pano_allows_unchanged_save(media_project):
    from novelvideo.creation.asset_reference import AssetReferences, protect_bindings
    from novelvideo.director_world import stage_manifest
    from novelvideo.utils.path_resolver import canonical_scene_360_path
    source, adapter = media_project
    root = Path(source.project_dir)
    directory = stage_manifest.stage_dir(root, 'Hall')
    directory.mkdir(parents=True)
    (directory / 'custom.sog').write_bytes(b'model')
    stage_manifest.update_manifest(root, 'Hall', custom_scene_path='custom.sog')
    pano = canonical_scene_360_path(root, 'Hall')
    pano.parent.mkdir(parents=True, exist_ok=True)
    pano.write_bytes(b'panorama')
    ref = await adapter.register_base('scene', 'Hall')
    data = AssetReferences(source.state_dir, root, 'p').materialize(ref,
        {'slot_id': 'scene:Hall:base:custom_scene', 'role': 'custom_scene'}, 'threeDWorldNode')['data']
    assert data['panoUrl']
    node = {'nodes': [{'id': 'n', 'type': 'threeDWorldNode', 'data': data}]}
    protect_bindings(node, node)


async def test_fixed_scene_layouts_remap_source_ids_without_active_leak(media_project):
    from novelvideo.creation.asset_reference import AssetReferences
    from novelvideo.director_world import stage_manifest
    from novelvideo.utils.static_urls import project_static_url
    source, adapter = media_project
    root = Path(source.project_dir)
    directory = stage_manifest.stage_dir(root, 'Hall')
    directory.mkdir(parents=True)
    ids = {}
    for kind in ('master', 'reverse', 'custom'):
        (directory / f'{kind}.sog').write_bytes(kind.encode())
        ids[kind] = f"legacy:{kind}:sog:{project_static_url('p', (directory / f'{kind}.sog').relative_to(root).as_posix())}"
    layouts = {ids[kind]: {'schemaVersion': 1, 'actors': [{'name': kind}], 'world': {'activeSourceId': ids[kind]}}
               for kind in ('master', 'reverse')}
    stage_manifest.update_manifest(root, 'Hall', master_ply_path='master.sog', reverse_ply_path='reverse.sog',
        custom_scene_path='custom.sog', active_source_id=ids['master'], scenes_by_source_id=layouts, scene=layouts[ids['master']])
    ref = await adapter.register_base('scene', 'Hall')
    # A later manifest edit must not influence fixed source/layout resolution.
    stage_manifest.update_manifest(root, 'Hall', scenes_by_source_id={}, scene=None)
    service = AssetReferences(source.state_dir, root, 'p')
    for role, expected in [('master_ply', 'master'), ('reverse_ply', 'reverse'), ('custom_scene', None)]:
        data = service.materialize(ref, {'slot_id': f'scene:Hall:base:{role}', 'role': role}, 'threeDWorldNode')['data']
        assert set(data['scenesBySourceId']) == {'master_ply', 'reverse_ply'}
        for key, value in data['scenesBySourceId'].items():
            assert value['world']['activeSourceId'] == key
        assert (data['scene']['actors'][0]['name'] if data['scene'] else None) == expected
        if data['scene']:
            assert data['scene']['world']['activeSourceId'] == role
        assert data['assetSceneManifest']['active_source']['id'] == role


async def test_readonly_media_api_hashing_runs_off_event_loop(tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace
    from novelvideo.api.routes import creation
    from novelvideo.creation.asset_reference import AssetReferences
    owner = threading.get_ident()
    workers = []
    async def context(*args):
        return SimpleNamespace(state_dir=tmp_path, output_dir=tmp_path, project_id='p')
    def catalog(self):
        workers.append(threading.get_ident())
        return []
    def materialize(self, *args):
        workers.append(threading.get_ident())
        return {'data': {}}
    monkeypatch.setattr(creation, 'context_for', context)
    monkeypatch.setattr(AssetReferences, 'catalog', catalog)
    monkeypatch.setattr(AssetReferences, 'materialize', materialize)
    await creation.browse_assets('p', {})
    await creation.resolve_asset_media('p', 'base', 1, 'slot', 'master', 'uploadNode', user={})
    assert len(workers) == 2 and all(worker != owner for worker in workers)


@pytest.mark.parametrize('role', ['pano', 'panorama_360'])
@pytest.mark.parametrize('id_form', ['scene-pano', 'source', 'manifest-source', 'explicit-fs'])
async def test_scene_pano_layout_identity_uses_fixed_source_evidence(media_project, role, id_form):
    from novelvideo.creation.asset_reference import AssetReferences
    from novelvideo.director_world import stage_manifest
    from novelvideo.utils.path_resolver import canonical_scene_360_path
    from novelvideo.utils.static_urls import project_static_url
    source, adapter = media_project
    root = Path(source.project_dir)
    directory = stage_manifest.stage_dir(root, 'Hall')
    directory.mkdir(parents=True)
    # Both roles exist; active_source evidence must choose the saved one's URL.
    paths = {'pano': directory / 'pano.png', 'panorama_360': canonical_scene_360_path(root, 'Hall')}
    for name, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
    url = project_static_url('p', paths[role].relative_to(root).as_posix())
    old_id = 'scene-pano:Hall' if id_form == 'scene-pano' else 'arbitrary:source:2' if id_form == 'explicit-fs' else f'{id_form}:pano:pano360:{url}'
    saved = {'schemaVersion': 1, 'actors': [{'name': role}], 'world': {'activeSourceId': old_id}}
    unknown_id = 'upstream-pano:other-node'
    unknown = {'schemaVersion': 1, 'actors': [{'name': 'unproven'}], 'world': {'activeSourceId': unknown_id}}
    active = {'id': old_id, 'source_type': 'pano360', 'source_kind': 'pano', 'pano_url': url,
              'slot_kind': 'scene_director_pano_360'} if id_form == 'scene-pano' else {}
    if id_form == 'explicit-fs':
        active = {'id': old_id, 'source_type': 'pano360', 'source_kind': 'pano', 'fs': str(paths[role])}
    stage_manifest.update_manifest(root, 'Hall', pano_path='pano.png', active_source_id=old_id, active_source=active,
        scenes_by_source_id={old_id: saved, unknown_id: unknown}, scene=saved)
    ref = await adapter.register_base('scene', 'Hall')
    stage_manifest.update_manifest(root, 'Hall', active_source={}, scenes_by_source_id={}, scene=None)
    service = AssetReferences(source.state_dir, root, 'p')
    for selected in paths:
        data = service.materialize(ref, {'slot_id': f'scene:Hall:base:{selected}', 'role': selected}, 'threeDWorldNode')['data']
        assert data['scenesBySourceId'][role]['world']['activeSourceId'] == role
        assert data['scenesBySourceId'][unknown_id] == unknown
        assert (data['scene']['actors'][0]['name'] if data['scene'] else None) == (role if selected == role else None)


@pytest.mark.parametrize('phase', ['catalog', 'archive'])
async def test_registration_io_does_not_block_loop_or_release_lock_on_cancel(media_project, monkeypatch, phase):
    import asyncio
    import threading
    from novelvideo.creation import asset_adapter
    from novelvideo.creation.asset_reference import AssetReferences
    from novelvideo.production_workflow.coordination import asset_project_lock
    source, adapter = media_project
    candidate(source, provisional=True)
    service = AssetReferences(source.state_dir, source.project_dir, 'p')
    entry = next(e for e in service.catalog() if e['source_name'] == 'Lin')
    owner_thread = threading.get_ident()
    entered, release = threading.Event(), threading.Event()
    threads = []
    original = service.catalog if phase == 'catalog' else asset_adapter.snapshot
    def slow(*args, **kwargs):
        threads.append(threading.get_ident())
        entered.set()
        if threading.get_ident() == owner_thread:
            raise AssertionError('registration heavy IO blocks event loop')
        assert release.wait(5)
        return original(*args, **kwargs)
    if phase == 'catalog':
        monkeypatch.setattr(service, 'catalog', slow)
    else:
        monkeypatch.setattr(asset_adapter, 'snapshot', slow)
    task = asyncio.create_task(service.register(entry['selection'], entry['proof'], actor='alice'))
    competitor = None
    acquired = asyncio.Event()
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert threads == [threads[0]] and threads[0] != owner_thread
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()  # repeated cancellation must not abandon the worker
        async def compete():
            async with asset_project_lock(source.state_dir):
                acquired.set()
        competitor = asyncio.create_task(compete())
        await asyncio.sleep(0.03)
        assert not task.done()
        assert not acquired.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(competitor, 2)
        with adapter.objects.transaction() as conn:
            assert conn.execute('SELECT count(*) FROM asset_sources').fetchone()[0] == 0
    finally:
        release.set()
        await asyncio.gather(task, *([competitor] if competitor else []), return_exceptions=True)


async def test_registration_only_hashes_selected_catalog_asset(media_project, monkeypatch):
    from novelvideo.creation import asset_reference
    source, _ = media_project
    service = asset_reference.AssetReferences(source.state_dir, source.project_dir, 'p')
    entry = next(e for e in service.catalog() if e['source_name'] == 'Lin')
    original = asset_reference.snapshot
    selected = []
    def checked(source, mapping, record, **kwargs):
        selected.append(mapping['source_name'])
        assert mapping['source_name'] == 'Lin'
        return original(source, mapping, record, **kwargs)
    monkeypatch.setattr(asset_reference, 'snapshot', checked)
    ref = await service.register(entry['selection'], entry['proof'], actor='alice')
    assert ref.revision == 1 and selected == ['Lin']
