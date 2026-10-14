import pytest
import sqlite3

from novelvideo.creation.asset_adapter import AssetAdapter
from novelvideo.creation.asset_sync import SourceSyncPending
from novelvideo.creation.models import AssetRef
from novelvideo.creation.store import ObjectStore, RevisionConflict
from novelvideo.models import NovelCharacter, NovelScene, CharacterIdentity
from novelvideo.sqlite_store import SQLiteStore


@pytest.fixture
async def assets(tmp_path):
    source = SQLiteStore('test', output_dir=str(tmp_path))
    await source.initialize()
    await source.add_character(NovelCharacter(name='Lin', age_group='youth'))
    await source.add_character_identity('Lin', CharacterIdentity(
        identity_id='original-id', character_name='Lin', identity_name='original', age_group='youth'))
    await source.add_scene(NovelScene(name='Hall', environment_prompt='stone hall'))
    adapter = AssetAdapter(source, ObjectStore(tmp_path))
    yield source, adapter
    await source.close()


async def test_character_import_create_retry_publish(assets):
    source, adapter = assets
    base = await adapter.register_base('character', 'Lin')
    assert await adapter.register_base('character', 'Lin') == base
    original = await adapter.register_variant(base.base_asset_id, 'original-id')
    assert (await adapter.resolve(original))['payload']['identity_id'] == 'original-id'
    variant = await adapter.create_variant(base.base_asset_id, 'coat', {'appearance_details': 'blue coat'}, 'r1')
    assert await adapter.create_variant(base.base_asset_id, 'coat', {'appearance_details': 'blue coat'}, 'r1') == variant
    assert len(source.get_character('Lin').identities) == 2
    assert (await adapter.resolve(variant))['payload']['age_group'] == 'youth'
    with pytest.raises(ValueError):
        await adapter.create_variant(base.base_asset_id, 'coat', {'appearance_details': 'red'}, 'r1')
    revised = await adapter.publish_revision(base.base_asset_id, variant.variant_id, 1, {'appearance_details': 'red coat'})
    assert revised.revision == 2
    assert (await adapter.resolve(variant))['payload']['appearance_details'] == 'blue coat'
    assert (await adapter.resolve(revised))['payload']['appearance_details'] == 'red coat'
    with pytest.raises(RevisionConflict):
        await adapter.publish_revision(base.base_asset_id, variant.variant_id, 1, {'appearance_details': 'bad'})
    with pytest.raises(ValueError):
        await adapter.publish_revision(base.base_asset_id, variant.variant_id, 2, {'reference_images': ['fake.png']})
    assert len(await adapter.list_variants(base.base_asset_id)) == 2


async def test_scene_variants_and_invalid_relationships(assets):
    source, adapter = assets
    base = await adapter.register_base('scene', 'Hall')
    variant = await adapter.create_variant(base.base_asset_id, 'night', {'time_of_day': 'night', 'variant_prompt': 'dark'}, 'scene1')
    row = await adapter.resolve(variant)
    assert row['payload']['base_scene_id'] == 'Hall'
    assert row['payload']['environment_prompt'] == ''
    with pytest.raises(ValueError):
        await adapter.create_variant(base.base_asset_id, 'Forest', {'location': 'Forest'}, 'scene2')
    await source.add_scene(NovelScene(name='Forest'))
    with pytest.raises(ValueError):
        await adapter.register_variant(base.base_asset_id, 'Forest')
    other = await adapter.register_base('scene', 'Forest')
    with pytest.raises(ValueError):
        await adapter.resolve(AssetRef(base_asset_id=other.base_asset_id, variant_id=variant.variant_id, revision=1))
    with pytest.raises(KeyError):
        await adapter.create_variant('missing', 'x', {}, 'missing')
    with pytest.raises(KeyError):
        await adapter.register_base('character', 'missing')


async def test_existing_scene_import_and_restart(assets):
    source, adapter = assets
    base = await adapter.register_base('scene', 'Hall')
    await source.add_scene(NovelScene(name='Hall night', base_scene_id='Hall', variant_id='night'))
    existing = await adapter.register_variant(base.base_asset_id, 'Hall night')
    result = await adapter.publish_revision(base.base_asset_id, existing.variant_id, 1, {'time_of_day': 'night'})
    assert (await source.get_scene_exact('Hall night')).time_of_day == 'night'
    restored = AssetAdapter(source, ObjectStore(source.project_dir))
    assert await restored.register_base('scene', 'Hall') == base
    assert (await restored.resolve(result))['payload']['time_of_day'] == 'night'
    char = await adapter.register_base('character', 'Lin')
    with pytest.raises(ValueError):
        await adapter.publish_revision(char.base_asset_id, result.variant_id, 2, {'age_group': 'child'})
    assert (await source.get_scene_exact('Hall night')).time_of_day == 'night'


async def test_reject_source_relationship_drift_before_publish(assets):
    source, adapter = assets
    base = await adapter.register_base('scene', 'Hall')
    variant = await adapter.create_variant(base.base_asset_id, 'night', {}, 'drift')
    locator = (await adapter.resolve(variant))['payload']['name']
    with pytest.raises(SourceSyncPending):
        await source.update_scene(locator, base_scene_id='different-location')
    with pytest.raises(SourceSyncPending):
        await adapter.publish_revision(base.base_asset_id, variant.variant_id, 1, {'time_of_day': 'night'})


async def test_retry_after_source_write_preserves_later_edits(assets, monkeypatch):
    source, adapter = assets
    base = await adapter.register_base('character', 'Lin')
    register = adapter._register
    async def fail(*args, **kwargs):
        raise RuntimeError('snapshot unavailable')
    monkeypatch.setattr(adapter, '_register', fail)
    with pytest.raises(RuntimeError):
        await adapter.create_variant(base.base_asset_id, 'coat', {'appearance_details': 'blue'}, 'retry')
    identity = source.get_character('Lin').identities[-1]
    await source.update_character_identity('Lin', identity.identity_id, appearance_details='later edit')
    monkeypatch.setattr(adapter, '_register', register)
    ref = await adapter.create_variant(base.base_asset_id, 'coat', {'appearance_details': 'blue'}, 'retry')
    assert len(source.get_character('Lin').identities) == 2
    assert (await adapter.resolve(ref))['payload']['appearance_details'] == 'later edit'


async def test_snapshot_failure_rolls_back_publish_metadata(assets, monkeypatch):
    source, adapter = assets
    base = await adapter.register_base('character', 'Lin')
    def fail(*args, **kwargs):
        raise RuntimeError('snapshot unavailable')
    monkeypatch.setattr(adapter.objects, 'update', fail)
    with pytest.raises(RuntimeError):
        await adapter.publish_revision(base.base_asset_id, None, 1, {'age_group': 'child'})
    assert source.get_character('Lin').age_group == 'youth'


async def test_separate_output_and_state_directories(tmp_path):
    source = SQLiteStore('split', output_dir=str(tmp_path / 'output'), state_dir=str(tmp_path / 'state'))
    await source.initialize()
    try:
        await source.add_character(NovelCharacter(name='A'))
        adapter = AssetAdapter(source, ObjectStore(tmp_path / 'state'))
        assert (await adapter.register_base('character', 'A')).revision == 1
    finally:
        await source.close()


async def test_stale_character_cache_fails_closed(assets):
    source, adapter = assets
    base = await adapter.register_base('character', 'Lin')
    other = SQLiteStore('test', output_dir=source.project_dir, state_dir=source.state_dir)
    await other.initialize()
    try:
        await other.add_character(source.get_character('Lin').model_copy(update={'age_group': 'elder'}))
        with pytest.raises(RuntimeError, match='stale'):
            await adapter.publish_revision(base.base_asset_id, None, 1, {'age_group': 'child'})
        assert (await other.get_character_from_graph('Lin')).age_group == 'elder'
    finally:
        await other.close()


async def test_pending_publication_fails_closed(assets):
    source, adapter = assets
    base = await adapter.register_base('character', 'Lin')
    with adapter.objects.transaction() as conn:
        conn.execute('INSERT INTO asset_publication_intents VALUES (?, ?, ?, ?)',
                     (base.base_asset_id, 1, '{}', '{}'))
    with pytest.raises(RuntimeError, match='reconciliation'):
        await adapter.publish_revision(base.base_asset_id, None, 1, {'age_group': 'child'})
    assert source.get_character('Lin').age_group == 'youth'


async def test_character_alias_uses_same_base_and_identity(assets):
    source, adapter = assets
    await source.add_character(source.get_character('Lin').model_copy(update={'aliases': ['Alias']}))
    canonical = await adapter.register_base('character', 'Lin')
    alias = await adapter.register_base('character', 'Alias')
    assert canonical == alias
    assert await adapter.register_variant(canonical.base_asset_id, 'original-id') == await adapter.register_variant(alias.base_asset_id, 'original-id')


async def test_external_audio_update_is_not_overwritten(assets):
    source, adapter = assets
    base = await adapter.register_base('character', 'Lin')
    other = SQLiteStore('test', output_dir=source.project_dir, state_dir=source.state_dir)
    await other.initialize()
    try:
        await other.add_character(source.get_character('Lin').model_copy(update={'reference_audio_path': 'published.wav'}))
        with pytest.raises(RuntimeError, match='stale'):
            await adapter.publish_revision(base.base_asset_id, None, 1, {'age_group': 'child'})
        fresh = await other.get_character_from_graph('Lin')
        assert fresh.reference_audio_path == 'published.wav'
        assert fresh.age_group == 'youth'
    finally:
        await other.close()


async def test_scene_source_reads_do_not_hold_objects_transaction(assets, monkeypatch):
    source, adapter = assets
    base = await adapter.register_base('scene', 'Hall')
    read = source.get_scene_exact
    async def checked_read(name):
        conn = sqlite3.connect(adapter.objects.db_path, timeout=0)
        try:
            conn.execute('BEGIN IMMEDIATE')
            conn.rollback()
        finally:
            conn.close()
        return await read(name)
    monkeypatch.setattr(source, 'get_scene_exact', checked_read)
    await adapter.create_variant(base.base_asset_id, 'night', {}, 'unlocked-read')


@pytest.mark.parametrize('raise_sql_error', [False, True])
async def test_identity_create_does_not_publish_failed_source_write(assets, monkeypatch, raise_sql_error):
    source, adapter = assets
    base = await adapter.register_base('character', 'Lin')
    async def failed_write(*args, **kwargs):
        if raise_sql_error:
            raise sqlite3.OperationalError('database write failed')
        return False
    monkeypatch.setattr(source, '_update_character_field', failed_write)
    with pytest.raises((RuntimeError, sqlite3.OperationalError), match='stale|database write failed'):
        await adapter.create_variant(base.base_asset_id, 'coat', {}, 'failed-create')
    assert await adapter.list_variants(base.base_asset_id) == []
    assert len((await source.get_character_from_graph('Lin')).identities) == 1


async def test_identity_publish_does_not_snapshot_failed_source_write(assets, monkeypatch):
    source, adapter = assets
    base = await adapter.register_base('character', 'Lin')
    variant = await adapter.register_variant(base.base_asset_id, 'original-id')
    async def failed_write(*args, **kwargs):
        return False
    monkeypatch.setattr(source, '_update_character_field', failed_write)
    with pytest.raises(RuntimeError, match='stale'):
        await adapter.publish_revision(base.base_asset_id, variant.variant_id, 1, {'appearance_details': 'coat'})
    with pytest.raises(SourceSyncPending):
        adapter.objects.read(variant.variant_id)
    assert adapter.objects.read(variant.variant_id, 1)['revision'] == 1
    assert (await source.get_character_from_graph('Lin')).identities[0].appearance_details == ''


async def test_failed_identity_compensation_retains_intent(assets, monkeypatch):
    source, adapter = assets
    base = await adapter.register_base('character', 'Lin')
    variant = await adapter.register_variant(base.base_asset_id, 'original-id')
    original_write = source._update_character_field
    calls = 0
    async def fail_compensation(*args, **kwargs):
        nonlocal calls
        calls += 1
        return await original_write(*args, **kwargs) if calls == 1 else False
    def failed_snapshot(*args, **kwargs):
        raise RuntimeError('snapshot failed')
    monkeypatch.setattr(source, '_update_character_field', fail_compensation)
    monkeypatch.setattr(adapter.objects, 'update', failed_snapshot)
    with pytest.raises(RuntimeError):
        await adapter.publish_revision(base.base_asset_id, variant.variant_id, 1, {'appearance_details': 'coat'})
    with adapter.objects.transaction() as conn:
        assert conn.execute('SELECT 1 FROM asset_publication_intents WHERE object_id=?', (variant.variant_id,)).fetchone()
    with pytest.raises(SourceSyncPending):
        adapter.objects.read(variant.variant_id)
    assert adapter.objects.read(variant.variant_id, 1)['revision'] == 1
