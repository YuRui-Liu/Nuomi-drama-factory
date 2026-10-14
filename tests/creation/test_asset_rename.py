"""Rename coordinates source identity, immutable history and recoverable media."""
import json
import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from novelvideo.creation.asset_adapter import AssetAdapter
from novelvideo.creation.store import ObjectStore
from novelvideo.models import CharacterIdentity, NovelCharacter, NovelEpisode, NovelScene
from novelvideo.sqlite_store import SQLiteStore


@pytest.fixture
async def project(tmp_path):
    source = SQLiteStore('test', output_dir=str(tmp_path / 'output'), state_dir=str(tmp_path / 'state'))
    await source.initialize()
    await source.add_character(NovelCharacter(name='Lin_old'))
    await source.add_character_identity('Lin_old', CharacterIdentity(
        identity_id='uuid_123_456', character_name='Lin_old', identity_name='coat_old'))
    await source.add_episodes([NovelEpisode(number=1, title='one', character_names=['Lin_old'],
        identity_ids=['uuid_123_456'], identity_default_map_json='{"Lin_old":"uuid_123_456"}')])
    adapter = AssetAdapter(source, ObjectStore(tmp_path / 'state'))
    base = await adapter.register_base('character', 'Lin_old')
    child = await adapter.register_variant(base.base_asset_id, 'uuid_123_456')
    yield source, adapter, base, child
    await source.close()


async def test_character_rename_retains_identity_and_fixed_history(project):
    source, adapter, base, child = project
    old = Path(source.project_dir) / 'assets/characters/Lin_old/identities/coat_old.png'
    old.parent.mkdir(parents=True)
    old.write_bytes(b'old immutable image')
    # The now-supported legacy sheet was installed after semantic registration.
    await adapter.refresh_source(base.base_asset_id, child.variant_id, expected_revision=1)
    await source.rename_character('Lin_old', 'Lin_new')
    char = source.get_character('Lin_new')
    assert char.identities[0].identity_id == 'uuid_123_456'
    assert char.identities[0].character_name == 'Lin_new'
    assert old.read_bytes() == b'old immutable image'
    assert (old.parents[1].with_name('Lin_new') / 'identities/coat_old.png').read_bytes() == old.read_bytes()
    ep = source.get_episode(1)
    assert ep.character_names == ['Lin_new']
    assert ep.identity_ids == ['uuid_123_456']
    assert ep.identity_default_map == {'Lin_new': 'uuid_123_456'}
    assert (await adapter.resolve(base))['payload']['name'] == 'Lin_old'
    assert adapter.objects.read(base.base_asset_id)['payload']['name'] == 'Lin_new'
    assert adapter.objects.read(child.variant_id)['revision'] == 3


async def test_identity_rename_uses_stored_name_and_preserves_uuid(project):
    source, adapter, base, child = project
    old = Path(source.project_dir) / 'assets/characters/Lin_old/identities/coat_old.png'
    old.parent.mkdir(parents=True)
    old.write_bytes(b'image')
    await adapter.refresh_source(base.base_asset_id, child.variant_id, expected_revision=1)
    await source.update_character_identity('Lin_old', 'uuid_123_456', identity_name='coat_new')
    assert source.get_character('Lin_old').identities[0].identity_id == 'uuid_123_456'
    assert old.exists() and old.with_name('coat_new.png').read_bytes() == b'image'
    assert adapter.objects.read(child.variant_id)['payload']['identity_name'] == 'coat_new'


async def test_conflicting_directory_has_no_source_or_history_side_effect(project):
    source, adapter, base, child = project
    (Path(source.project_dir) / 'assets/characters/Lin_new').mkdir(parents=True)
    with pytest.raises(ValueError):
        await source.rename_character('Lin_old', 'Lin_new')
    assert source.get_character('Lin_old') is not None
    assert adapter.objects.read(base.base_asset_id)['revision'] == 1


@pytest.mark.parametrize('phase', ['prepared', 'sql_committed', 'media_installed', 'before_semantic_commit', 'semantic_committed'])
@pytest.mark.parametrize('cancel', [False, True])
async def test_every_durable_window_recovers_idempotently(project, monkeypatch, phase, cancel):
    from novelvideo.creation import asset_rename
    from novelvideo.creation.asset_sync import SourceSyncPending
    source, adapter, base, child = project
    def interrupt(at):
        if at == phase:
            raise asyncio.CancelledError() if cancel else RuntimeError('simulated crash')
    monkeypatch.setattr(asset_rename, '_checkpoint', interrupt)
    with pytest.raises(asyncio.CancelledError if cancel else SourceSyncPending):
        await source.rename_character('Lin_old', 'Lin_new', description='one request')
    assert (await adapter.resolve(base))['payload']['name'] == 'Lin_old'
    if phase != 'semantic_committed':
        with pytest.raises(SourceSyncPending):
            adapter.objects.read(base.base_asset_id)
    with pytest.raises(SourceSyncPending):
        await source.update_character('Lin_new', description='concurrent')
    monkeypatch.setattr(asset_rename, '_checkpoint', lambda phase: None)
    await adapter.reconcile_source()
    await adapter.reconcile_source()
    await source.rename_character('Lin_old', 'Lin_new', description='one request')
    assert adapter.objects.read(base.base_asset_id)['revision'] == 2
    assert adapter.objects.read(base.base_asset_id)['payload']['description'] == 'one request'
    assert adapter.objects.read(child.variant_id)['revision'] == 2


async def test_recovery_refuses_sql_drift_and_explicit_refresh(project, monkeypatch):
    from novelvideo.creation import asset_rename
    from novelvideo.creation.asset_sync import SourceSyncPending
    source, adapter, base, child = project
    def interrupt(phase):
        if phase == 'sql_committed':
            raise RuntimeError('crash')
    monkeypatch.setattr(asset_rename, '_checkpoint', interrupt)
    with pytest.raises(SourceSyncPending):
        await source.rename_character('Lin_old', 'Lin_new')
    monkeypatch.setattr(asset_rename, '_checkpoint', lambda phase: None)
    with sqlite3.connect(source.db_path) as conn:
        conn.execute("UPDATE characters SET description='external writer' WHERE name='Lin_new'")
    with pytest.raises(SourceSyncPending, match='drift'):
        await adapter.reconcile_source()
    with pytest.raises(SourceSyncPending):
        await adapter.refresh_source(base.base_asset_id, expected_revision=1, acknowledge_pending=True)
    assert (await source.list_characters())[0].description == 'external writer'
    assert (await adapter.resolve(base))['payload']['name'] == 'Lin_old'


async def test_workflow_alias_reads_and_writes_retained_slot(project):
    from novelvideo.production_workflow.store import ProductionWorkflowStore
    source, adapter, base, child = project
    media = Path(source.project_dir) / 'assets/characters/Lin_old/portrait.png'
    media.parent.mkdir(parents=True)
    media.write_bytes(b'original')
    path = Path(source.state_dir) / 'production_workflow.json'
    workflow = ProductionWorkflowStore(path)
    slot, original = workflow.materialize_legacy_current(slot_id='character:Lin_old:portrait',
        asset_kind='character_portrait', asset_path=str(media))
    # Workflow was seeded after registration; capture that explicit media change.
    await adapter.refresh_source(base.base_asset_id, expected_revision=1)
    await source.rename_character('Lin_old', 'Lin_new')
    workflow = ProductionWorkflowStore(path)
    assert workflow.get_slot('character:Lin_new:portrait')[0].current_version_id == original.version_id
    workflow.register_candidate_version(slot_id='character:Lin_new:portrait', asset_kind='character_portrait',
        version_id='new-version', asset_path=str(media), source_attempt_id=None, qc_passed=True,
        generation_metadata=None, actor='test', at=datetime.now(timezone.utc))
    workflow.adopt_version(slot_id='character:Lin_new:portrait', version_id='new-version',
        actor='test', reason='selected', at=datetime.now(timezone.utc))
    saved = json.loads(path.read_text())
    assert set(saved['slots']) == {'character:Lin_old:portrait'}
    assert all(v['slot_id'] == 'character:Lin_old:portrait' for v in saved['versions'])
    assert Path(workflow.get_slot('character:Lin_new:portrait')[1][original.version_id].asset_path).read_bytes() == b'original'


async def test_scene_base_and_child_keep_semantic_relationships_and_manifest(project):
    from novelvideo.director_world import stage_manifest
    source, adapter, _, _ = project
    await source.add_scene(NovelScene(name='Hall'))
    await source.add_scene(NovelScene(name='Hall_night', base_scene_id='Hall', variant_id='night'))
    base = await adapter.register_base('scene', 'Hall')
    child = await adapter.register_variant(base.base_asset_id, 'Hall_night')
    stage_manifest.save_manifest(Path(source.project_dir), 'Hall', {'scene_id': 'Hall', 'version': 'v1'})
    old_manifest = stage_manifest.manifest_path(Path(source.project_dir), 'Hall')
    await source.rename_scene('Hall', 'Room', description='new room')
    assert (await source.get_scene_exact('Hall_night')).base_scene_id == 'Room'
    assert adapter.objects.read(child.variant_id)['revision'] == 2
    assert adapter.objects.read(base.base_asset_id)['revision'] == 2
    assert (await adapter.resolve(base))['payload']['name'] == 'Hall'
    assert json.loads(old_manifest.read_text())['scene_id'] == 'Hall'
    assert stage_manifest.load_manifest(Path(source.project_dir), 'Room')['scene_id'] == 'Room'
    await source.rename_scene('Hall_night', 'Room_evening')
    assert adapter.objects.read(child.variant_id)['revision'] == 3
    assert adapter.objects.read(child.variant_id)['payload']['name'] == 'Room_evening'


async def test_identity_duplicate_name_conflicts_without_side_effect(project):
    source, adapter, base, child = project
    await source.add_character_identity('Lin_old', CharacterIdentity(identity_id='other',
        character_name='Lin_old', identity_name='already'))
    revision = adapter.objects.read(base.base_asset_id)['revision']
    with pytest.raises(ValueError, match='already exists'):
        await source.update_character_identity('Lin_old', 'uuid_123_456', identity_name='already')
    assert adapter.objects.read(base.base_asset_id)['revision'] == revision
    assert source.get_character('Lin_old').identities[0].identity_name == 'coat_old'


async def test_media_journal_contains_digests_not_binary_payload(project):
    source, adapter, base, child = project
    path = Path(source.project_dir) / 'assets/characters/Lin_old/binary.bin'
    path.parent.mkdir(parents=True)
    data = bytes(range(256)) * 8192
    path.write_bytes(data)
    await source.rename_character('Lin_old', 'Lin_new')
    with sqlite3.connect(source.db_path) as conn:
        evidence = conn.execute('SELECT evidence FROM asset_rename_intents').fetchone()[0]
    assert len(evidence) < 30000
    assert 'sha256' in evidence
    assert (path.parent.with_name('Lin_new') / path.name).read_bytes() == data


async def test_actual_patch_combines_metadata_and_retries_after_committed_source(project, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.api.routes import characters
    from novelvideo.api.schemas import CharacterUpdate
    from novelvideo.creation import asset_rename
    from novelvideo.creation.asset_sync import SourceSyncPending
    source, adapter, base, child = project
    async def resolve(*args, **kwargs):
        return (SimpleNamespace(), 'admin', 'test', Path(source.project_dir), Path(source.project_dir), source)
    monkeypatch.setattr(characters, '_resolve_character_project', resolve)
    def crash(phase):
        if phase == 'sql_committed':
            raise RuntimeError('restart before response')
    monkeypatch.setattr(asset_rename, '_checkpoint', crash)
    body = CharacterUpdate(name='Lin_new', face_prompt='new face')
    with pytest.raises(SourceSyncPending):
        await characters.update_character('test', 'Lin_old', body, {})
    # Restart cache, just as a new request/worker would observe the committed SQL.
    await source.load_graph_state()
    monkeypatch.setattr(asset_rename, '_checkpoint', lambda phase: None)
    result = await characters.update_character('test', 'Lin_old', body, {})
    assert result['ok']
    assert (await characters.update_character('test', 'Lin_old', body, {}))['ok']
    assert adapter.objects.read(base.base_asset_id)['revision'] == 2
    assert source.get_character('Lin_new').face_prompt == 'new face'


async def test_actual_scene_patch_handles_children_and_retry(project, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.api.routes import scenes
    from novelvideo.api.schemas import SceneUpdate
    source, adapter, _, _ = project
    await source.add_scene(NovelScene(name='Hall'))
    await source.add_scene(NovelScene(name='Hall_night', base_scene_id='Hall', variant_id='night'))
    base = await adapter.register_base('scene', 'Hall')
    child = await adapter.register_variant(base.base_asset_id, 'Hall_night')
    async def resolve(*args, **kwargs):
        return (SimpleNamespace(project_id='test', state_dir=source.state_dir, is_home_node=True),
                'admin', 'test', Path(source.project_dir), Path(source.project_dir), source)
    monkeypatch.setattr(scenes, '_resolve_scene_project', resolve)
    body = SceneUpdate(name='Room', description='same request')
    first = await scenes.update_scene('test', 'Hall', body, {})
    second = await scenes.update_scene('test', 'Hall', body, {})
    assert first['ok'] and second['ok']
    assert second['data']['description'] == 'same request'
    assert (await source.get_scene_exact('Hall_night')).base_scene_id == 'Room'
    assert adapter.objects.read(base.base_asset_id)['revision'] == 2
    assert adapter.objects.read(child.variant_id)['revision'] == 2


async def test_scene_state_slot_alias_keeps_existing_adopted_version(project):
    from novelvideo.production_workflow.store import ProductionWorkflowStore
    source, adapter, _, _ = project
    await source.add_scene(NovelScene(name='Hall'))
    await source.add_scene(NovelScene(name='Hall_night', base_scene_id='Hall', variant_id='night'))
    path = Path(source.state_dir) / 'production_workflow.json'
    workflow = ProductionWorkflowStore(path)
    _, version = workflow.materialize_legacy_current(slot_id='scene:Hall:state:Hall_night:master',
        asset_kind='scene_state', asset_path='old/immutable.png')
    await source.rename_scene('Hall_night', 'Hall_evening')
    workflow = ProductionWorkflowStore(path)
    slot, versions = workflow.get_slot('scene:Hall:state:Hall_evening:master')
    assert slot.current_version_id == version.version_id
    assert versions[version.version_id].asset_path == 'old/immutable.png'
    await source.rename_scene('Hall', 'Room')
    workflow = ProductionWorkflowStore(path)
    assert workflow.get_slot('scene:Room:state:Hall_evening:master')[0].current_version_id == slot.current_version_id


@pytest.mark.parametrize('corruption', ['collision', 'cycle'])
async def test_workflow_conflicts_fail_before_sql(project, corruption):
    from novelvideo.production_workflow.store import ProductionWorkflowStore
    source, adapter, base, child = project
    path = Path(source.state_dir) / 'production_workflow.json'
    workflow = ProductionWorkflowStore(path)
    for name in ['Lin_old', 'Lin_new'] if corruption == 'collision' else ['Lin_old']:
        workflow.materialize_legacy_current(slot_id=f'character:{name}:portrait',
            asset_kind='character_portrait', asset_path=f'{name}.png')
    if corruption == 'cycle':
        saved = json.loads(path.read_text())
        saved['slot_aliases'] = {'a': 'b', 'b': 'a'}
        path.write_text(json.dumps(saved))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        await source.rename_character('Lin_old', 'Lin_new')
    assert path.read_bytes() == before
    assert adapter.objects.read(base.base_asset_id)['revision'] == 1
    assert source.get_character('Lin_old') is not None


async def test_recovery_never_overwrites_changed_media(project, monkeypatch):
    from novelvideo.creation import asset_rename
    from novelvideo.creation.asset_sync import SourceSyncPending
    source, adapter, base, child = project
    old = Path(source.project_dir) / 'assets/characters/Lin_old/p.png'
    old.parent.mkdir(parents=True)
    old.write_bytes(b'original')
    def crash(phase):
        if phase == 'sql_committed':
            raise RuntimeError('restart')
    monkeypatch.setattr(asset_rename, '_checkpoint', crash)
    with pytest.raises(SourceSyncPending):
        await source.rename_character('Lin_old', 'Lin_new')
    old.write_bytes(b'external edit')
    monkeypatch.setattr(asset_rename, '_checkpoint', lambda phase: None)
    with pytest.raises(SourceSyncPending, match='media drifted'):
        await adapter.reconcile_source()
    assert old.read_bytes() == b'external edit'
    assert not old.parent.with_name('Lin_new').exists()


async def test_legacy_rename_does_not_create_semantic_database(tmp_path):
    source = SQLiteStore('test', output_dir=str(tmp_path))
    await source.initialize()
    try:
        await source.add_character(NovelCharacter(name='old'))
        await source.rename_character('old', 'new')
        assert not (tmp_path / 'creation').exists()
        assert source.get_character('new') is not None
    finally:
        await source.close()


async def test_two_renames_keep_one_slot_and_real_current_consumer(project):
    from types import SimpleNamespace
    from PIL import Image
    from novelvideo.production_workflow.store import ProductionWorkflowStore
    from novelvideo.narrative_groups.reference_refresh import _current_character_media
    source, adapter, base, child = project
    root = Path(source.project_dir)
    media = root / 'assets/characters/Lin_old/portrait.png'
    media.parent.mkdir(parents=True)
    Image.new('RGB', (64, 64), 'red').save(media)
    path = Path(source.state_dir) / 'production_workflow.json'
    workflow = ProductionWorkflowStore(path)
    _, original = workflow.materialize_legacy_current(slot_id='character:Lin_old:portrait',
        asset_kind='character_portrait', asset_path=str(media.relative_to(root)))
    await adapter.refresh_source(base.base_asset_id, expected_revision=1)
    await source.rename_character('Lin_old', 'Lin_middle')
    await source.rename_character('Lin_middle', 'Lin_final')
    workflow = ProductionWorkflowStore(path)
    slot, versions = workflow.get_slot('character:Lin_final:portrait')
    assert slot.slot_id == 'character:Lin_final:portrait'
    assert versions[original.version_id].asset_path == 'assets/characters/Lin_final/portrait.png'
    assert workflow.get_slot('character:Lin_old:portrait')[1][original.version_id].asset_path == 'assets/characters/Lin_old/portrait.png'
    assert workflow.list_slots()[0].slot_id == 'character:Lin_final:portrait'
    available, _ = _current_character_media(SimpleNamespace(output_dir=root, state_dir=source.state_dir), source.get_all_characters())
    assert available == {'Lin_final'}
    workflow.register_candidate_version(slot_id='character:Lin_final:portrait', asset_kind='character_portrait',
        version_id='final-candidate', asset_path='assets/characters/Lin_final/portrait.png',
        source_attempt_id=None, qc_passed=True, generation_metadata=None, actor='test', at=datetime.now(timezone.utc))
    workflow.adopt_version(slot_id='character:Lin_final:portrait', version_id='final-candidate',
        actor='test', reason='chosen', at=datetime.now(timezone.utc))
    saved = json.loads(path.read_text())
    assert set(saved['slots']) == {'character:Lin_old:portrait'}
    assert next(v for v in saved['versions'] if v['version_id'] == original.version_id)['asset_path'] == 'assets/characters/Lin_old/portrait.png'


async def test_unrelated_source_and_workflow_edits_survive_recovery(project, monkeypatch):
    from novelvideo.creation import asset_rename
    from novelvideo.creation.asset_sync import SourceSyncPending
    from novelvideo.production_workflow.store import ProductionWorkflowStore
    source, adapter, base, child = project
    await source.add_character(NovelCharacter(name='Other'))
    other = await adapter.register_base('character', 'Other')
    path = Path(source.state_dir) / 'production_workflow.json'
    workflow = ProductionWorkflowStore(path)
    (Path(source.project_dir) / 'old.png').write_bytes(b'old')
    workflow.materialize_legacy_current(slot_id='character:Lin_old:portrait', asset_kind='character_portrait', asset_path='old.png')
    await adapter.refresh_source(base.base_asset_id, expected_revision=1)
    def crash(phase):
        if phase == 'sql_committed':
            raise RuntimeError('interrupted')
    monkeypatch.setattr(asset_rename, '_checkpoint', crash)
    with pytest.raises(SourceSyncPending):
        await source.rename_character('Lin_old', 'Lin_new')
    await source.update_character('Other', description='independent edit')
    assert adapter.objects.read(other.base_asset_id)['revision'] == 2
    workflow = ProductionWorkflowStore(path)
    (Path(source.project_dir) / 'other.png').write_bytes(b'other')
    workflow.materialize_legacy_current(slot_id='character:Other:portrait', asset_kind='character_portrait', asset_path='other.png')
    monkeypatch.setattr(asset_rename, '_checkpoint', lambda phase: None)
    await adapter.reconcile_source()
    assert source.get_character('Other').description == 'independent edit'
    assert ProductionWorkflowStore(path).get_slot('character:Other:portrait')[0].current_version_id


async def test_character_rename_repaths_declared_identity_media_and_voice(project):
    from novelvideo.utils.path_resolver import canonical_identity_portrait_path, compute_identity_portrait_path
    source, adapter, base, child = project
    root = Path(source.project_dir)
    portrait = canonical_identity_portrait_path(root, 'Lin_old', 'coat_old')
    portrait.parent.mkdir(parents=True)
    portrait.write_bytes(b'face')
    voice = root / 'assets/characters/Lin_old/voices/default.wav'
    voice.parent.mkdir(parents=True)
    voice.write_bytes(b'audio')
    # The fixture installs canonical bytes after registration; explicitly
    # capture that media revision before the metadata/rename under test.
    await adapter.refresh_source(base.base_asset_id, child.variant_id, expected_revision=1)
    await source.update_character_identity('Lin_old', 'uuid_123_456', portrait_image=str(portrait),
        reference_images=[str(portrait)], reference_audio_path=str(voice.relative_to(root)))
    old_revision = adapter.objects.read(child.variant_id)['revision']
    await source.rename_character('Lin_old', 'Lin_new')
    identity = source.get_character('Lin_new').identities[0]
    assert '/Lin_new/' in identity.portrait_image
    assert identity.reference_images == [identity.portrait_image]
    assert identity.reference_audio_path == 'assets/characters/Lin_new/voices/default.wav'
    assert Path(compute_identity_portrait_path(root, 'Lin_new', 'coat_old')).read_bytes() == b'face'
    assert adapter.objects.read(child.variant_id, old_revision)['payload']['portrait_image'] == str(portrait)
    assert portrait.read_bytes() == b'face'


async def test_owned_partial_staging_is_resumed_without_overwriting_source(project, monkeypatch):
    from novelvideo.creation import asset_rename
    from novelvideo.creation.asset_sync import SourceSyncPending
    source, adapter, base, child = project
    root = Path(source.project_dir) / 'assets/characters/Lin_old'
    root.mkdir(parents=True)
    (root / 'first.png').write_bytes(b'first')
    (root / 'second.png').write_bytes(b'second')
    original = asset_rename.os.link
    calls = 0
    def fail_after_one(old, new):
        nonlocal calls
        calls += 1
        original(old, new)
        if calls == 1:
            raise RuntimeError('crash after first staging install')
    monkeypatch.setattr(asset_rename.os, 'link', fail_after_one)
    with pytest.raises(SourceSyncPending):
        await source.rename_character('Lin_old', 'Lin_new')
    assert not root.with_name('Lin_new').exists()
    monkeypatch.setattr(asset_rename.os, 'link', original)
    await adapter.reconcile_source()
    assert (root.with_name('Lin_new') / 'first.png').read_bytes() == b'first'
    assert (root.with_name('Lin_new') / 'second.png').read_bytes() == b'second'
    assert (root / 'first.png').read_bytes() == b'first'


async def test_identity_rename_copies_portrait_costume_and_retains_versions(project):
    from novelvideo.utils.path_resolver import canonical_identity_portrait_path, canonical_identity_costume_path
    source, adapter, base, child = project
    root = Path(source.project_dir)
    portrait = canonical_identity_portrait_path(root, 'Lin_old', 'coat_old')
    costume = canonical_identity_costume_path(root, 'Lin_old', 'coat_old')
    portrait.parent.mkdir(parents=True)
    portrait.write_bytes(b'face')
    costume.write_bytes(b'costume')
    version = portrait.parent / 'coat_old/versions/immutable.png'
    version.parent.mkdir(parents=True)
    version.write_bytes(b'history')
    await adapter.refresh_source(base.base_asset_id, child.variant_id, expected_revision=1)
    await source.update_character_identity('Lin_old', 'uuid_123_456',
        portrait_image=str(portrait), costume_image=str(costume))
    await source.update_character_identity('Lin_old', 'uuid_123_456', identity_name='coat_new')
    identity = source.get_character('Lin_old').identities[0]
    assert Path(identity.portrait_image) == canonical_identity_portrait_path(root, 'Lin_old', 'coat_new')
    assert Path(identity.costume_image).read_bytes() == b'costume'
    assert version.read_bytes() == b'history'
    assert (version.parents[2] / 'coat_new/versions/immutable.png').read_bytes() == b'history'


async def test_scene_alias_projects_adoption_metadata_without_rewriting_history(project):
    from novelvideo.production_workflow.store import ProductionWorkflowStore
    from novelvideo.api.routes.production_assets import _scene_slot_canonical_relative_path
    source, adapter, _, _ = project
    await source.add_scene(NovelScene(name='Hall'))
    root = Path(source.project_dir)
    media = root / 'assets/scenes/Hall/versions/v1.png'
    media.parent.mkdir(parents=True)
    media.write_bytes(b'original')
    path = Path(source.state_dir) / 'production_workflow.json'
    workflow = ProductionWorkflowStore(path)
    workflow.register_candidate_version(slot_id='scene:Hall:base:master', asset_kind='scene_base', version_id='v1',
        asset_path='assets/scenes/Hall/versions/v1.png', source_attempt_id=None, qc_passed=True,
        generation_metadata={'scene_id': 'Hall', 'anchor_kind': 'master', 'canonical_path': 'assets/scenes/Hall/master.png'},
        actor='test', at=datetime.now(timezone.utc))
    await source.rename_scene('Hall', 'Room')
    await source.rename_scene('Room', 'Atrium')
    workflow = ProductionWorkflowStore(path)
    slot, versions = workflow.get_slot('scene:Atrium:base:master')
    current = versions['v1']
    assert current.asset_path == 'assets/scenes/Atrium/versions/v1.png'
    assert _scene_slot_canonical_relative_path(slot.slot_id, current.generation_metadata) == Path('assets/scenes/Atrium/master.png')
    assert current.generation_metadata['canonical_path'] == 'assets/scenes/Atrium/master.png'
    workflow.adopt_version(slot_id=slot.slot_id, version_id='v1', actor='test', reason='retain', at=datetime.now(timezone.utc))
    saved = json.loads(path.read_text())
    assert saved['versions'][0]['generation_metadata']['scene_id'] == 'Hall'
    assert saved['versions'][0]['asset_path'] == 'assets/scenes/Hall/versions/v1.png'


async def test_completed_retry_cannot_succeed_for_a_different_reused_base(project):
    source, adapter, base, child = project
    await source.rename_character('Lin_old', 'Lin_new')
    # Simulate an unsupported external writer bypassing locator retirement.
    with sqlite3.connect(source.db_path) as conn:
        conn.execute("INSERT INTO characters (name,description) VALUES ('Lin_old','different asset')")
    await source.load_graph_state()
    with pytest.raises(ValueError, match='reused'):
        await source.rename_character('Lin_old', 'Lin_new')
    assert source.get_character('Lin_old').description == 'different asset'
    assert adapter.objects.read(base.base_asset_id)['revision'] == 2


def _asset_state(source, adapter):
    result = []
    for path in (source.db_path, adapter.objects.db_path):
        with sqlite3.connect(path) as conn:
            result.append(tuple(conn.iterdump()))
    workflow = Path(source.state_dir) / 'production_workflow.json'
    result.append(workflow.read_bytes() if workflow.exists() else None)
    return result


async def _renamed_locator(project, kind):
    from novelvideo.production_workflow.store import ProductionWorkflowStore
    source, adapter, base, _ = project
    old, new = ('Lin_old', 'Lin_new') if kind == 'character' else ('Hall', 'Room')
    if kind == 'scene':
        await source.add_scene(NovelScene(name=old))
        base = await adapter.register_base(kind, old)
    suffix = 'portrait' if kind == 'character' else 'base:master'
    workflow = ProductionWorkflowStore(Path(source.state_dir) / 'production_workflow.json')
    (Path(source.project_dir) / 'original.png').write_bytes(b'original')
    workflow.materialize_legacy_current(slot_id=f'{kind}:{old}:{suffix}',
        asset_kind='character_portrait' if kind == 'character' else 'scene_base', asset_path='original.png')
    await adapter.refresh_source(base.base_asset_id, expected_revision=1)
    await getattr(source, f'rename_{kind}')(old, new)
    return old, new, base, suffix, workflow


@pytest.mark.parametrize('kind', ['character', 'scene'])
@pytest.mark.parametrize('entry', ['single', 'batch', 'analysis_or_if_absent', 'rename_target', 'register'])
async def test_retired_asset_locator_rejected_before_source_or_registry_write(project, kind, entry):
    source, adapter, _, _ = project
    old, new, _, _, _ = await _renamed_locator(project, kind)
    model = NovelCharacter if kind == 'character' else NovelScene
    if entry == 'rename_target':
        await getattr(source, f'add_{kind}')(model(name='Unrelated'))
    if entry == 'register':
        # External SQL can create a conflicting row but registration must not
        # assign a new semantic owner to an already-owned workflow locator.
        with sqlite3.connect(source.db_path) as conn:
            conn.execute(f'INSERT INTO {kind}s (name) VALUES (?)', (old,))
        await source.load_graph_state()
    before = _asset_state(source, adapter)
    with pytest.raises(ValueError, match='retired'):
        if entry == 'single':
            await getattr(source, f'add_{kind}')(model(name=old))
        elif entry == 'batch':
            await getattr(source, f'add_{kind}s_atomic')([model(name='Unrelated'), model(name=old)])
        elif entry == 'analysis_or_if_absent':
            if kind == 'character':
                await source.publish_character_analysis_atomic('unused', [model(name=old)], {})
            else:
                await source.add_scene_if_absent(model(name=old))
        elif entry == 'rename_target':
            await getattr(source, f'rename_{kind}')('Unrelated', old)
        else:
            await adapter.register_base(kind, old)
    assert _asset_state(source, adapter) == before


@pytest.mark.parametrize('kind', ['character', 'scene'])
@pytest.mark.parametrize('mutation', ['register', 'adopt', 'materialize', 'delete', 'retarget', 'new_slot'])
async def test_retired_workflow_mutations_reject_even_stale_store_but_reads_remain(project, kind, mutation):
    source, adapter, _, _ = project
    old, new, _, suffix, stale = await _renamed_locator(project, kind)
    slot_id = f'{kind}:{old}:{suffix}'
    slot, versions = stale.get_slot(slot_id)
    version_id = slot.current_version_id
    before = _asset_state(source, adapter)
    with pytest.raises(ValueError, match='retired'):
        if mutation in ('register', 'new_slot'):
            stale.register_candidate_version(slot_id=slot_id if mutation == 'register' else f'{kind}:{old}:other',
                asset_kind=slot.asset_kind, version_id='intruder', asset_path='new-A-image', source_attempt_id=None,
                qc_passed=True, generation_metadata=None, actor='test', at=datetime.now(timezone.utc))
        elif mutation == 'adopt':
            stale.adopt_version(slot_id=slot_id, version_id=version_id, actor='test', reason='', at=datetime.now(timezone.utc))
        elif mutation == 'materialize':
            stale.materialize_legacy_current(slot_id=slot_id, asset_kind=slot.asset_kind, asset_path='new-A-image')
        elif mutation == 'delete':
            stale.delete_version(slot_id=slot_id, version_id=version_id)
        else:
            stale.retarget_version_asset_paths(slot_id=slot_id, version_ids=(version_id,), asset_path='new-A-image')
    assert _asset_state(source, adapter) == before
    assert stale.get_slot(slot_id)[1][version_id].asset_path == 'original.png'
    assert stale.read_legacy_current(slot_id=slot_id, asset_kind=slot.asset_kind, asset_path='fallback')[1].version_id == version_id


@pytest.mark.parametrize('kind', ['character', 'scene'])
async def test_latest_name_allows_update_publish_and_repeated_rename(project, kind):
    from novelvideo.production_workflow.store import ProductionWorkflowStore
    source, adapter, _, _ = project
    old, new, base, suffix, _ = await _renamed_locator(project, kind)
    model = NovelCharacter if kind == 'character' else NovelScene
    # Upsert of the current source is not locator reuse.
    current = source.get_character(new) if kind == 'character' else await source.get_scene_exact(new)
    await getattr(source, f'add_{kind}')(current.model_copy(update={'description': 'ordinary update'}))
    revision = adapter.objects.read(base.base_asset_id)['revision']
    await adapter.publish_revision(base.base_asset_id, None, revision,
                                  {'face_prompt': 'updated'} if kind == 'character' else {'description': 'updated'})
    await getattr(source, f'rename_{kind}')(new, 'Final')
    workflow = ProductionWorkflowStore(Path(source.state_dir) / 'production_workflow.json')
    for retired in (old, new):
        with pytest.raises(ValueError, match='retired'):
            workflow.materialize_legacy_current(slot_id=f'{kind}:{retired}:{suffix}', asset_kind='unused', asset_path='intruder')
        with pytest.raises(ValueError, match='retired'):
            await getattr(source, f'add_{kind}')(model(name=retired))
    latest = f'{kind}:Final:{suffix}'
    slot, _ = workflow.get_slot(latest)
    workflow.register_candidate_version(slot_id=latest, asset_kind=slot.asset_kind, version_id='latest',
        asset_path='current-image', source_attempt_id=None, qc_passed=True, generation_metadata=None,
        actor='test', at=datetime.now(timezone.utc))
    workflow.adopt_version(slot_id=latest, version_id='latest', actor='test', reason='chosen', at=datetime.now(timezone.utc))
    assert workflow.get_slot(latest)[0].current_version_id == 'latest'
    await getattr(source, f'add_{kind}')(model(name='Fresh'))
    assert (await adapter.register_base(kind, 'Fresh')).base_asset_id != base.base_asset_id


@pytest.mark.parametrize('kind', ['character', 'scene'])
async def test_concurrent_create_waits_for_rename_then_rejects_old_name(project, monkeypatch, kind):
    source, adapter, _, _ = project
    if kind == 'scene':
        await source.add_scene(NovelScene(name='Old'))
    else:
        await source.add_character(NovelCharacter(name='Old'))
    ready, release = asyncio.Event(), asyncio.Event()
    original = source.load_graph_state
    async def paused_reload():
        ready.set()
        await release.wait()
        return await original()
    monkeypatch.setattr(source, 'load_graph_state', paused_reload)
    rename = asyncio.create_task(getattr(source, f'rename_{kind}')('Old', 'Current'))
    await ready.wait()
    model = NovelCharacter if kind == 'character' else NovelScene
    create = asyncio.create_task(getattr(source, f'add_{kind}s_atomic')([model(name='Fresh'), model(name='Old')]))
    await asyncio.sleep(0)
    assert not create.done()
    release.set()
    await rename
    before = _asset_state(source, adapter)
    with pytest.raises(ValueError, match='retired'):
        await create
    assert _asset_state(source, adapter) == before


async def test_scene_state_retired_name_cannot_be_used_for_base_or_new_state_slot(project):
    from novelvideo.production_workflow.store import ProductionWorkflowStore
    source, adapter, _, _ = project
    await source.add_scene(NovelScene(name='Hall'))
    await source.add_scene(NovelScene(name='Night', base_scene_id='Hall', variant_id='night'))
    path = Path(source.state_dir) / 'production_workflow.json'
    workflow = ProductionWorkflowStore(path)
    workflow.materialize_legacy_current(slot_id='scene:Hall:state:Night:master', asset_kind='scene_state', asset_path='night.png')
    await source.rename_scene('Night', 'Evening')
    before = _asset_state(source, adapter)
    for retired in ('scene:Hall:state:Night:panorama', 'scene:Night:base:master'):
        with pytest.raises(ValueError, match='retired'):
            workflow.materialize_legacy_current(slot_id=retired, asset_kind='scene_state', asset_path='intruder')
    with pytest.raises(ValueError, match='retired'):
        await source.add_scene(NovelScene(name='Night'))
    assert _asset_state(source, adapter) == before
    workflow.materialize_legacy_current(slot_id='scene:Hall:state:Evening:master', asset_kind='scene_state', asset_path='evening.png')


@pytest.mark.parametrize('kind', ['character', 'scene'])
async def test_legacy_media_only_rename_reserves_old_name_without_creation_database(tmp_path, kind):
    from novelvideo.production_workflow.store import ProductionWorkflowStore
    source = SQLiteStore('test', output_dir=str(tmp_path))
    await source.initialize()
    model = NovelCharacter if kind == 'character' else NovelScene
    try:
        await getattr(source, f'add_{kind}')(model(name='Old'))
        media = tmp_path / 'assets' / (kind + 's') / 'Old' / 'image.png'
        media.parent.mkdir(parents=True)
        media.write_bytes(b'history')
        await getattr(source, f'rename_{kind}')('Old', 'Current')
        with pytest.raises(ValueError, match='retired'):
            await getattr(source, f'add_{kind}')(model(name='Old'))
        assert media.read_bytes() == b'history'
        assert not (Path(source.state_dir) / 'creation').exists()
        workflow = ProductionWorkflowStore(Path(source.state_dir) / 'production_workflow.json')
        workflow.read_legacy_current(slot_id=f'{kind}:Old:portrait', asset_kind=kind, asset_path=str(media))
        assert not (Path(source.state_dir) / 'creation').exists()
    finally:
        await source.close()


async def test_scene_variant_retired_locator_does_not_leave_request_journal(project):
    source, adapter, _, _ = project
    await source.add_scene(NovelScene(name='Hall'))
    base = await adapter.register_base('scene', 'Hall')
    original = await adapter.create_variant(base.base_asset_id, 'night', {}, 'initial')
    old = adapter.objects.read(original.variant_id)['payload']['name']
    await source.rename_scene(old, 'Evening')
    before = _asset_state(source, adapter)
    with pytest.raises(ValueError, match='retired'):
        await adapter.create_variant(base.base_asset_id, 'night', {}, 'different-request')
    assert _asset_state(source, adapter) == before
    assert await adapter.create_variant(base.base_asset_id, 'night', {}, 'initial') == original


async def test_legacy_creation_freezes_name_before_await_so_retirement_cannot_be_bypassed(tmp_path, monkeypatch):
    source = SQLiteStore('test', output_dir=str(tmp_path))
    await source.initialize()
    try:
        await source.add_scene(NovelScene(name='Old'))
        await source.rename_scene('Old', 'Current')
        ready, release = asyncio.Event(), asyncio.Event()
        original = source._ensure_db
        async def paused_ensure():
            ready.set()
            await release.wait()
            return await original()
        monkeypatch.setattr(source, '_ensure_db', paused_ensure)
        model = NovelScene(name='Fresh')
        create = asyncio.create_task(source.add_scene(model))
        await ready.wait()
        model.name = 'Old'
        release.set()
        await create
        assert await source.get_scene_exact('Old') is None
        assert await source.get_scene_exact('Fresh') is not None
    finally:
        await source.close()


async def test_uuid_identity_locator_survives_identity_then_character_rename(project):
    source, adapter, base, _ = project
    identity_id = '9d050784-60e5-4cae-ae1c-8fae0e42b973'
    await source.add_character_identity('Lin_old', CharacterIdentity(identity_id=identity_id,
        character_name='Lin_old', identity_name='young_age_coat'))
    child = await adapter.register_variant(base.base_asset_id, identity_id)
    await source.update_character_identity('Lin_old', identity_id, identity_name='older_age_coat')
    await source.rename_character('Lin_old', 'Lin_new')
    current = adapter.objects.read(child.variant_id)
    assert current['payload']['identity_id'] == identity_id
    assert current['payload']['identity_name'] == 'older_age_coat'
    assert current['payload']['character_name'] == 'Lin_new'
    assert current['revision'] == 3
    assert (await adapter.resolve(child))['payload']['identity_name'] == 'young_age_coat'


async def test_identity_editor_can_reapply_metadata_after_completed_rename(project):
    source, adapter, base, child = project
    await source.update_character_identity('Lin_old', 'uuid_123_456', identity_name='new', appearance_details='coat')
    await source.update_character_identity('Lin_old', 'uuid_123_456', appearance_details='changed later')
    await source.update_character_identity('Lin_old', 'uuid_123_456', identity_name='new', appearance_details='coat')
    assert source.get_character('Lin_old').identities[0].appearance_details == 'coat'
    assert adapter.objects.read(child.variant_id)['revision'] == 4


@pytest.mark.parametrize('rename_variant', [False, True])
@pytest.mark.parametrize('phase', [None, 'prepared', 'sql_committed', 'before_semantic_commit'])
async def test_scene_rename_coordinates_episode_menu_references(project, monkeypatch, rename_variant, phase):
    from novelvideo.creation import asset_rename
    from novelvideo.creation.asset_sync import SourceSyncPending
    source, adapter, _, _ = project
    await source.add_scene(NovelScene(name='Hall'))
    await source.add_scene(NovelScene(name='Hall_night', base_scene_id='Hall', variant_id='night'))
    base = await adapter.register_base('scene', 'Hall')
    child = await adapter.register_variant(base.base_asset_id, 'Hall_night')
    menu = [{'scene_id': 'Hall'},
            {'scene_id': 'Hall_night', 'base_scene_id': 'Hall', 'variant_id': 'night', 'notes': 'Hall'},
            {'scene_id': 'Hallway'}, 'Hall']
    with sqlite3.connect(source.db_path) as conn:
        conn.execute('UPDATE episodes SET scene_menu_json=?, raw_content=? WHERE number=1',
                     (json.dumps(menu), 'Hall and Hall_night stay in the script'))
    old, new = ('Hall_night', 'Hall_evening') if rename_variant else ('Hall', 'GrandHall')
    expected = json.loads(json.dumps(menu))
    if rename_variant:
        expected[1]['scene_id'] = new
    else:
        expected[0]['scene_id'] = new
        expected[1]['base_scene_id'] = new
        expected[3] = new
    def crash(at):
        if at == phase:
            raise RuntimeError('episode rename checkpoint')
    monkeypatch.setattr(asset_rename, '_checkpoint', crash)
    if phase:
        with pytest.raises(SourceSyncPending):
            await source.rename_scene(old, new)
        with sqlite3.connect(source.db_path) as conn:
            saved = json.loads(conn.execute('SELECT scene_menu_json FROM episodes WHERE number=1').fetchone()[0])
        assert saved == (menu if phase == 'prepared' else expected)
        monkeypatch.setattr(asset_rename, '_checkpoint', lambda phase: None)
        await adapter.reconcile_source()
    else:
        await source.rename_scene(old, new)
    with sqlite3.connect(source.db_path) as conn:
        saved, script = conn.execute('SELECT scene_menu_json,raw_content FROM episodes WHERE number=1').fetchone()
        evidence = json.loads(conn.execute('SELECT evidence FROM asset_rename_intents').fetchone()[0])
    assert json.loads(saved) == expected
    assert script == 'Hall and Hall_night stay in the script'
    assert any(c['table'] == 'episodes' and c['before']['number'] == 1 for c in evidence['changes'])
    assert (await adapter.resolve(base))['payload']['name'] == 'Hall'
    assert (await adapter.resolve(child))['payload']['name'] == 'Hall_night'
