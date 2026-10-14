"""Registered source writes advance immutable semantic history or fail closed."""

import asyncio
import sqlite3
from pathlib import Path

import pytest

from novelvideo.creation.asset_adapter import AssetAdapter
from novelvideo.creation.store import ObjectStore
from novelvideo.models import CharacterIdentity, NovelCharacter, NovelScene
from novelvideo.sqlite_store import SQLiteStore


@pytest.fixture
async def registered(tmp_path):
    source = SQLiteStore('test', output_dir=str(tmp_path))
    await source.initialize()
    await source.add_character(NovelCharacter(name='Lin', age_group='youth'))
    await source.add_character_identity('Lin', CharacterIdentity(
        identity_id='original', character_name='Lin', identity_name='original'))
    await source.add_scene(NovelScene(name='Hall'))
    adapter = AssetAdapter(source, ObjectStore(tmp_path))
    base = await adapter.register_base('character', 'Lin')
    child = await adapter.register_variant(base.base_asset_id, 'original')
    scene = await adapter.register_base('scene', 'Hall')
    yield source, adapter, base, child, scene
    await source.close()


@pytest.mark.parametrize('kind', ['character', 'identity', 'scene', 'bulk_character', 'bulk_scene'])
async def test_source_metadata_advances_affected_history_once(registered, kind):
    source, adapter, base, child, scene = registered
    if kind == 'character':
        await source.update_character('Lin', appearance_details='coat')
    elif kind == 'identity':
        await source.update_character_identity('Lin', 'original', appearance_details='coat')
    elif kind == 'scene':
        await source.update_scene('Hall', description='stone')
    elif kind == 'bulk_character':
        await source.add_characters_atomic([NovelCharacter(name='Lin', description='new')], skip_existing=False)
    else:
        await source.add_scenes_atomic([NovelScene(name='Hall', description='new')], skip_existing=False)
    expected = (2, 2, 1) if kind == 'identity' else ((1, 1, 2) if 'scene' in kind else (2, 1, 1))
    assert tuple(adapter.objects.read(ref.variant_id or ref.base_asset_id)['revision']
                 for ref in (base, child, scene)) == expected
    character = (await source.list_characters())[0]
    assert adapter.objects.read(base.base_asset_id)['payload']['identities_json'] == character.identities_json
    assert adapter.objects.read(base.base_asset_id)['payload']['appearance_details'] == character.appearance_details
    assert adapter.objects.read(child.variant_id)['payload']['appearance_details'] == character.identities[0].appearance_details
    assert adapter.objects.read(scene.base_asset_id)['payload']['description'] == (await source.get_scene_exact('Hall')).description
    assert (await adapter.resolve(base))['payload']['age_group'] == 'youth'
    assert (await adapter.resolve(child))['payload']['appearance_details'] == ''
    assert (await adapter.resolve(scene))['payload']['description'] == ''


async def test_noop_timestamp_and_unregistered_writes_do_not_version(registered):
    source, adapter, base, child, scene = registered
    await source.update_character('Lin', age_group='youth')
    await source.update_character_identity('Lin', 'original', appearance_details='')
    await source.update_scene('Hall', notes='')
    await source.add_character(NovelCharacter(name='Unregistered'))
    assert [adapter.objects.read(ref.variant_id or ref.base_asset_id)['revision']
            for ref in (base, child, scene)] == [1, 1, 1]


async def test_legacy_write_never_initializes_creation(tmp_path):
    source = SQLiteStore('test', output_dir=str(tmp_path))
    await source.initialize()
    try:
        await source.add_character(NovelCharacter(name='Lin'))
        await source.update_character('Lin', description='new')
        assert not (tmp_path / 'creation').exists()
    finally:
        await source.close()


async def test_cross_store_writes_use_sql_and_serialize_revisions(registered):
    source, adapter, base, child, scene = registered
    other = SQLiteStore('test', output_dir=source.project_dir)
    await other.initialize()
    try:
        await asyncio.gather(source.update_character('Lin', description='first'),
                             other.update_character('Lin', appearance_details='second'))
        row = adapter.objects.read(base.base_asset_id)
        assert row['revision'] == 3
        assert (row['payload']['description'], row['payload']['appearance_details']) == ('first', 'second')
    finally:
        await other.close()


async def test_adapter_nested_publication_versions_target_and_parent_once(registered):
    source, adapter, base, child, scene = registered
    new_child = await adapter.create_variant(base.base_asset_id, 'coat', {}, 'create')
    assert adapter.objects.read(base.base_asset_id)['revision'] == 2
    published = await adapter.publish_revision(base.base_asset_id, child.variant_id, 1, {'appearance_details': 'coat'})
    assert published.revision == 2
    assert adapter.objects.read(base.base_asset_id)['revision'] == 3
    assert adapter.objects.read(new_child.variant_id)['revision'] == 1


async def test_committed_source_sync_failure_blocks_current_and_recovers(registered, monkeypatch):
    source, adapter, base, child, scene = registered
    assert hasattr(adapter, 'reconcile_source')
    from novelvideo.creation.asset_sync import SourceSync, SourceSyncPending
    finish = SourceSync.apply
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('snapshot disk failure')
    monkeypatch.setattr(SourceSync, 'apply', fail)
    with pytest.raises(SourceSyncPending, match='may already be committed'):
        await source.update_character_identity('Lin', 'original', appearance_details='coat')
    assert (await source.list_characters())[0].identities[0].appearance_details == 'coat'
    assert (await adapter.resolve(child))['payload']['appearance_details'] == ''
    for ref in (base, child):
        with pytest.raises(SourceSyncPending):
            adapter.objects.read(ref.variant_id or ref.base_asset_id)
    with pytest.raises(SourceSyncPending):
        await adapter.list_variants(base.base_asset_id)
    with pytest.raises(SourceSyncPending):
        await adapter.register_base('character', 'Lin')
    with pytest.raises(SourceSyncPending):
        await adapter.publish_revision(base.base_asset_id, child.variant_id, 1, {'appearance_details': 'new'})
    monkeypatch.setattr(SourceSync, 'apply', finish)
    refs = await adapter.reconcile_source()
    assert len(refs) == 2
    assert await adapter.reconcile_source() == []
    assert adapter.objects.read(child.variant_id)['revision'] == 2
    assert adapter.objects.read(base.base_asset_id)['revision'] == 2


async def test_unknown_commit_window_requires_explicit_cas_confirmation(registered, monkeypatch):
    source, adapter, base, child, scene = registered
    assert hasattr(adapter, 'refresh_source')
    from novelvideo.creation.asset_sync import SourceSync, SourceSyncPending
    from novelvideo.creation.store import RevisionConflict
    capture = SourceSync.capture_after
    def crash(*args, **kwargs):
        raise RuntimeError('process stopped before evidence')
    monkeypatch.setattr(SourceSync, 'capture_after', crash)
    with pytest.raises(SourceSyncPending):
        await source.update_character('Lin', description='committed')
    monkeypatch.setattr(SourceSync, 'capture_after', capture)
    with pytest.raises(SourceSyncPending, match='evidence'):
        await adapter.reconcile_source()
    with pytest.raises(RevisionConflict):
        await adapter.refresh_source(base.base_asset_id, expected_revision=9, acknowledge_pending=True)
    with pytest.raises(SourceSyncPending):
        await adapter.refresh_source(base.base_asset_id, expected_revision=1)
    revised = await adapter.refresh_source(base.base_asset_id, expected_revision=1, acknowledge_pending=True)
    assert revised.revision == 2
    assert (await adapter.resolve(revised))['payload']['description'] == 'committed'
    with adapter.objects.transaction() as conn:
        assert conn.execute('SELECT * FROM asset_source_sync_resolutions').fetchone()
    await adapter.reconcile_source()


async def test_reconcile_never_overwrites_conflicting_semantic_revision(registered, monkeypatch):
    source, adapter, base, child, scene = registered
    assert hasattr(adapter, 'reconcile_source')
    from novelvideo.creation.asset_sync import SourceSync, SourceSyncPending
    from novelvideo.creation.store import RevisionConflict
    finish = SourceSync.apply
    monkeypatch.setattr(SourceSync, 'apply', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('stop')))
    with pytest.raises(SourceSyncPending):
        await source.update_character('Lin', description='source')
    with adapter.objects.transaction() as conn:
        conn.execute("INSERT INTO object_versions SELECT object_id,2,payload,'other',created_at FROM object_versions WHERE object_id=?", (base.base_asset_id,))
        conn.execute('UPDATE objects SET current_revision=2 WHERE id=?', (base.base_asset_id,))
    monkeypatch.setattr(SourceSync, 'apply', finish)
    with pytest.raises(RevisionConflict):
        await adapter.reconcile_source()
    with pytest.raises(SourceSyncPending):
        adapter.objects.read(base.base_asset_id)


@pytest.mark.parametrize('operation', ['delete'])
async def test_missing_locator_retains_pending_history(registered, operation):
    source, adapter, base, child, scene = registered
    assert hasattr(adapter, 'reconcile_source')
    from novelvideo.creation.asset_sync import SourceSyncPending
    with pytest.raises(SourceSyncPending):
        if operation == 'rename':
            await source.rename_character('Lin', 'New')
        else:
            await source.delete_character('Lin')
    with pytest.raises(SourceSyncPending):
        await adapter.reconcile_source()
    assert (await adapter.resolve(base))['payload']['name'] == 'Lin'


async def test_unrelated_asset_remains_writable_while_another_is_pending(registered, monkeypatch):
    source, adapter, base, child, scene = registered
    from novelvideo.creation.asset_sync import SourceSync, SourceSyncPending
    await source.add_character(NovelCharacter(name='Other'))
    other = await adapter.register_base('character', 'Other')
    finish = SourceSync.apply
    monkeypatch.setattr(SourceSync, 'apply', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('stop')))
    with pytest.raises(SourceSyncPending):
        await source.update_character('Lin', description='source')
    monkeypatch.setattr(SourceSync, 'apply', finish)
    await source.update_character('Other', description='independent')
    assert adapter.objects.read(other.base_asset_id)['revision'] == 2
    with pytest.raises(SourceSyncPending):
        adapter.objects.read(base.base_asset_id)


@pytest.mark.parametrize('capture', [False, True])
async def test_cancellation_after_source_commit_preserves_evidence(registered, monkeypatch, capture):
    source, adapter, base, child, scene = registered
    from novelvideo.creation.asset_sync import SourceSync, SourceSyncPending
    original = SourceSync.capture_after
    def cancel(self, *args):
        if capture:
            original(self, *args)
        raise asyncio.CancelledError()
    monkeypatch.setattr(SourceSync, 'capture_after', cancel)
    with pytest.raises(asyncio.CancelledError):
        await source.update_character('Lin', description='committed')
    with pytest.raises(SourceSyncPending):
        adapter.objects.read(base.base_asset_id)
    assert (await source.list_characters())[0].description == 'committed'
    monkeypatch.setattr(SourceSync, 'capture_after', original)
    if capture:
        await adapter.reconcile_source()
    else:
        with pytest.raises(SourceSyncPending, match='evidence'):
            await adapter.reconcile_source()


async def test_sql_rollback_clears_only_proven_noop_journal(registered, monkeypatch):
    source, adapter, base, child, scene = registered
    db = await source._ensure_db()
    execute = db.execute
    def fail(sql, *args, **kwargs):
        if sql.startswith('INSERT INTO characters'):
            raise sqlite3.OperationalError('source failed')
        return execute(sql, *args, **kwargs)
    monkeypatch.setattr(db, 'execute', fail)
    with pytest.raises(sqlite3.OperationalError, match='source failed'):
        await source.update_character('Lin', description='bad')
    assert adapter.objects.read(base.base_asset_id)['revision'] == 1
    assert await adapter.reconcile_source() == []


async def test_pending_source_allows_fixed_asset_bind_but_not_current_assertion(registered):
    from novelvideo.creation.asset_sync import SourceSync, SourceSyncPending
    from novelvideo.creation.models import OperationRequest
    from novelvideo.creation.operations import OperationService
    from novelvideo.freezone.orchestration.projection import create_canvas, patch_graph
    from novelvideo.production_workflow.coordination import asset_project_lock
    source, adapter, base, child, scene = registered
    from novelvideo.utils.path_resolver import canonical_identity_portrait_path
    path = canonical_identity_portrait_path(Path(source.project_dir), 'Lin', 'original')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'fixed portrait')
    # Explicitly enrich this fixture before creating the pending source write.
    child = await adapter.refresh_source(base.base_asset_id, child.variant_id, expected_revision=child.revision)
    async with asset_project_lock(source.state_dir):
        await SourceSync(source, adapter.objects).prepare('character')
    episode = adapter.objects.create('episode', {}, 'actor')
    with adapter.objects.transaction() as conn:
        conn.execute('INSERT INTO episode_canvases VALUES (?, ?)', (episode['id'], 'canvas'))
        conn.execute("INSERT INTO canvas_workspaces VALUES ('canvas','episode',?,'test','fixture','fixture')", (episode['id'],))
    create_canvas(Path(source.state_dir), 'canvas', project_id='p', name='test', actor_id='actor')
    patch_graph(Path(source.state_dir), 'canvas', base_revision=1, request_id='seed', operations=[
        {'op': 'add_node', 'node': {'id': 'n', 'type': 'uploadNode', 'data': {}, 'position': {'x': 0, 'y': 0}}}])
    service = OperationService(adapter.objects, project_id='p', authorize=lambda *args: True)
    request = OperationRequest(request_id='bind', kind='asset.bind',
        scope=dict(project_id='p', episode_id=episode['id'], canvas_id='canvas', node_ids=['n']),
        expected_revisions={'canvas': 2}, payload={'node_id': 'n', 'asset_ref': child.model_dump(),
            'media': {'slot_id': 'character:Lin:identity:original:portrait', 'role': 'portrait'}})
    assert service.apply('actor', request).status == 'running'
    with pytest.raises(SourceSyncPending):
        service.apply('actor', request.model_copy(update={'request_id': 'bind-current',
            'expected_revisions': {'canvas': 2, child.variant_id: child.revision}}))


async def test_registered_missing_source_reports_pending_on_registration(registered):
    source, adapter, base, child, scene = registered
    from novelvideo.creation.asset_sync import SourceSyncPending
    with pytest.raises(SourceSyncPending):
        await source.delete_character('Lin')
    with pytest.raises(SourceSyncPending):
        await adapter.register_base('character', 'Lin')


@pytest.mark.parametrize('persistent', [False, True])
async def test_postcommit_cache_error_is_explicitly_pending(registered, monkeypatch, persistent):
    source, adapter, base, child, scene = registered
    from novelvideo.creation.asset_sync import SourceSyncPending
    reload = source._reload_source_character_cache
    calls = 0
    async def fail_after_write():
        nonlocal calls
        calls += 1
        if calls == 2 or (persistent and calls > 2):
            raise sqlite3.OperationalError('cache read failed')
        await reload()
    monkeypatch.setattr(source, '_reload_source_character_cache', fail_after_write)
    with pytest.raises(SourceSyncPending, match='may already be committed') as failure:
        await source.update_character('Lin', description='committed')
    assert isinstance(failure.value.__cause__, sqlite3.OperationalError)
    assert str(failure.value.__cause__) == 'cache read failed'
    assert (await source.list_characters())[0].description == 'committed'
    with pytest.raises(SourceSyncPending):
        adapter.objects.read(base.base_asset_id)


async def test_prewrite_persistent_cache_failure_keeps_original_error(registered, monkeypatch):
    source, adapter, base, child, scene = registered
    async def fail_reload():
        raise sqlite3.OperationalError('cache unavailable before write')
    monkeypatch.setattr(source, '_reload_source_character_cache', fail_reload)
    with pytest.raises(sqlite3.OperationalError, match='cache unavailable before write'):
        await source.update_character('Lin', description='must not write')
    assert (await source.list_characters())[0].description == ''
    assert adapter.objects.read(base.base_asset_id)['revision'] == 1


async def test_cleanup_failure_cannot_replace_existing_pending_error(registered, monkeypatch):
    source, adapter, base, child, scene = registered
    from novelvideo.creation.asset_sync import SourceSync, SourceSyncPending
    reload = source._reload_source_character_cache
    calls = 0
    async def fail_cleanup():
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise sqlite3.OperationalError('cleanup failed')
        await reload()
    def fail_snapshot(*args, **kwargs):
        raise sqlite3.OperationalError('snapshot failed')
    monkeypatch.setattr(source, '_reload_source_character_cache', fail_cleanup)
    monkeypatch.setattr(SourceSync, 'apply', fail_snapshot)
    with pytest.raises(SourceSyncPending) as failure:
        await source.update_character('Lin', description='committed')
    assert str(failure.value.__cause__) == 'snapshot failed'


async def test_write_after_capture_rejects_changed_journal(registered, monkeypatch):
    source, adapter, base, child, scene = registered
    from novelvideo.creation.asset_sync import SourceSync, SourceSyncPending
    records = SourceSync.records
    calls = 0
    async def tamper(self, mappings):
        nonlocal calls
        actual = await records(self, mappings)
        calls += 1
        if calls == 2:
            with adapter.objects.transaction() as conn:
                conn.execute("UPDATE asset_source_sync SET actor_id='other' WHERE object_id=?", (base.base_asset_id,))
        return actual
    monkeypatch.setattr(SourceSync, 'records', tamper)
    with pytest.raises(SourceSyncPending):
        await source.update_character('Lin', description='committed')
    with adapter.objects.transaction() as conn:
        row = conn.execute('SELECT * FROM asset_source_sync WHERE object_id=?', (base.base_asset_id,)).fetchone()
    assert row['actor_id'] == 'other'
    assert row['source_after'] is None


async def test_publication_skip_is_not_inherited_by_child_task(registered):
    from novelvideo.creation.asset_sync import publication_source_write, SourceSyncPending
    from novelvideo.production_workflow.coordination import asset_project_lock
    source, adapter, base, child, scene = registered
    with adapter.objects.transaction() as conn:
        conn.execute('INSERT INTO asset_publication_intents VALUES (?,1,?,?)', (base.base_asset_id, '{}', '{}'))
    async with asset_project_lock(source.state_dir):
        with publication_source_write(source.state_dir, base.base_asset_id):
            task = asyncio.create_task(source.update_character('Lin', description='must not write'))
            await asyncio.sleep(0)
            assert not task.done()
    with pytest.raises(SourceSyncPending):
        await asyncio.wait_for(task, 2)
    assert (await source.list_characters())[0].description == ''


async def test_explicit_refresh_uses_sql_despite_stale_cache_and_is_noop_safe(registered):
    source, adapter, base, child, scene = registered
    with sqlite3.connect(source.db_path) as conn:
        conn.execute("UPDATE characters SET description='SQL truth' WHERE name='Lin'")
    source.get_character('Lin').description = 'stale cache'
    ref = await adapter.refresh_source(base.base_asset_id, expected_revision=1)
    assert ref.revision == 2
    assert (await adapter.resolve(ref))['payload']['description'] == 'SQL truth'
    assert await adapter.refresh_source(base.base_asset_id, expected_revision=2) == ref


async def test_preexisting_drift_cannot_be_reconciled_without_confirmation(registered):
    source, adapter, base, child, scene = registered
    from novelvideo.creation.asset_sync import SourceSyncPending
    with sqlite3.connect(source.db_path) as conn:
        conn.execute("UPDATE characters SET description='preexisting drift' WHERE name='Lin'")
    with pytest.raises(SourceSyncPending, match='drift before write'):
        await source.update_character('Lin', appearance_details='must not write')
    actual = (await source.list_characters())[0]
    assert actual.description == 'preexisting drift'
    assert actual.appearance_details == ''
    with pytest.raises(SourceSyncPending, match='evidence'):
        await adapter.reconcile_source()
    with pytest.raises(SourceSyncPending):
        adapter.objects.read(base.base_asset_id)
    assert (await adapter.resolve(base))['payload']['description'] == ''
    confirmed = await adapter.refresh_source(base.base_asset_id, expected_revision=1, acknowledge_pending=True)
    assert confirmed.revision == 2
    assert (await adapter.resolve(confirmed))['payload']['description'] == 'preexisting drift'
    with adapter.objects.transaction() as conn:
        assert conn.execute('SELECT * FROM asset_source_sync_resolutions WHERE object_id=?', (base.base_asset_id,)).fetchone()
    await adapter.reconcile_source()


async def test_bulk_inputs_cannot_change_after_journal_targets_are_selected(registered, monkeypatch):
    source, adapter, base, child, scene = registered
    from novelvideo.creation.asset_sync import SourceSync
    await source.add_character(NovelCharacter(name='Other'))
    other = await adapter.register_base('character', 'Other')
    items = [NovelCharacter(name='Lin', description='requested')]
    read = SourceSync.records
    async def mutate(self, mappings):
        result = await read(self, mappings)
        items.append(NovelCharacter(name='Other', description='late mutation'))
        return result
    monkeypatch.setattr(SourceSync, 'records', mutate)
    await source.add_characters_atomic(items, skip_existing=False)
    assert (await source.get_character_from_graph('Other')).description == ''
    assert adapter.objects.read(other.base_asset_id)['revision'] == 1
