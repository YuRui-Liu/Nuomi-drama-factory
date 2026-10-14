"""The legacy source boundary also protects adapter and direct-store writes."""

import asyncio
import sqlite3

import pytest

from novelvideo.creation.asset_adapter import AssetAdapter
from novelvideo.creation.store import ObjectStore
from novelvideo.models import CharacterIdentity, NovelCharacter, NovelScene
from novelvideo.production_workflow.coordination import asset_project_lock
from novelvideo.sqlite_store import SQLiteStore


@pytest.fixture
async def source(tmp_path):
    store = SQLiteStore('test', output_dir=str(tmp_path))
    await store.initialize()
    await store.add_character(NovelCharacter(name='Lin', description='original'))
    await store.add_character_identity('Lin', CharacterIdentity(
        identity_id='id', identity_name='original', character_name='Lin'))
    await store.add_scene(NovelScene(name='Hall'))
    yield store
    await store.close()


async def test_stale_character_cache_does_not_overwrite_unrelated_sql_fields(source):
    with sqlite3.connect(source.db_path) as conn:
        conn.execute("UPDATE characters SET description='external' WHERE name='Lin'")
    await source.update_character('Lin', appearance_details='coat')
    stored = (await source.list_characters())[0]
    assert stored.description == 'external'
    assert stored.appearance_details == 'coat'
    assert source.get_character('Lin').description == 'external'


async def test_two_stores_preserve_different_field_edits(source):
    other = SQLiteStore('test', output_dir=source.project_dir)
    await other.initialize()
    await other.load_graph_state()
    try:
        await asyncio.gather(source.update_character('Lin', description='first'),
                             other.update_character('Lin', appearance_details='second'))
        stored = (await source.list_characters())[0]
        assert (stored.description, stored.appearance_details) == ('first', 'second')
    finally:
        await other.close()


@pytest.mark.parametrize('operation', ['character', 'identity', 'scene', 'rename_character', 'rename_scene',
                                     'character_batch', 'scene_batch'])
async def test_low_level_writes_wait_for_shared_project_boundary(source, operation):
    async def write():
        if operation == 'character':
            await source.update_character('Lin', description='changed')
        elif operation == 'identity':
            await source.update_character_identity('Lin', 'id', appearance_details='changed')
        elif operation == 'scene':
            await source.update_scene('Hall', notes='changed')
        elif operation == 'rename_character':
            await source.rename_character('Lin', 'New')
        elif operation == 'rename_scene':
            await source.rename_scene('Hall', 'New')
        elif operation == 'character_batch':
            await source.add_characters_atomic([NovelCharacter(name='New')])
        else:
            await source.add_scenes_atomic([NovelScene(name='New')])
    async with asset_project_lock(source.state_dir):
        task = asyncio.create_task(write())
        await asyncio.sleep(.05)
        waiting = not task.done()
    await task
    assert waiting, 'Direct source write bypassed the shared project lock'


@pytest.mark.parametrize('operation', ['character', 'identity', 'delete_all'])
async def test_sql_failure_surfaces_and_restores_character_cache(source, monkeypatch, operation):
    db = await source._ensure_db()
    execute = db.execute
    def fail(sql, *args, **kwargs):
        if sql.startswith(('UPDATE characters', 'INSERT INTO characters', 'DELETE FROM characters')):
            raise sqlite3.OperationalError('injected SQL failure')
        return execute(sql, *args, **kwargs)
    monkeypatch.setattr(db, 'execute', fail)
    with pytest.raises(sqlite3.OperationalError, match='injected SQL failure'):
        if operation == 'character':
            await source.update_character('Lin', description='not persisted')
        elif operation == 'identity':
            await source.update_character_identity('Lin', 'id', appearance_details='not persisted')
        else:
            await source.delete_all_characters()
    assert source.get_character('Lin').description == 'original'
    assert source.get_character('Lin').identities[0].appearance_details == ''


async def test_adapter_can_nest_source_write_without_extra_revision(source):
    adapter = AssetAdapter(source, ObjectStore(source.project_dir))
    ref = await adapter.register_base('character', 'Lin')
    revised = await asyncio.wait_for(adapter.publish_revision(
        ref.base_asset_id, None, 1, {'appearance_details': 'coat'}), 2)
    assert revised.revision == 2
    assert (await adapter.resolve(ref))['payload']['appearance_details'] == ''


async def test_adapter_and_direct_edit_preserve_both_fields(source, monkeypatch):
    adapter = AssetAdapter(source, ObjectStore(source.project_dir))
    ref = await adapter.register_base('character', 'Lin')
    db = await source._ensure_db()
    commit = db.commit
    entered, proceed = asyncio.Event(), asyncio.Event()
    async def pause_first_commit():
        if not entered.is_set():
            entered.set()
            await proceed.wait()
        await commit()
    monkeypatch.setattr(db, 'commit', pause_first_commit)
    publication = asyncio.create_task(adapter.publish_revision(
        ref.base_asset_id, None, 1, {'appearance_details': 'coat'}))
    await entered.wait()
    direct = asyncio.create_task(source.update_character('Lin', description='edited'))
    await asyncio.sleep(.03)
    waiting = not direct.done()
    proceed.set()
    revised, _ = await asyncio.gather(publication, direct)
    assert waiting
    stored = (await source.list_characters())[0]
    assert (stored.description, stored.appearance_details) == ('edited', 'coat')
    assert revised.revision == 2
    assert (await adapter.resolve(ref))['payload']['appearance_details'] == ''


async def test_legacy_reads_and_writes_do_not_create_semantic_database(source):
    from pathlib import Path
    await source.update_character('Lin', description='legacy')
    await source.update_scene('Hall', notes='legacy')
    await source.list_characters()
    await source.get_scene('Hall')
    assert not (Path(source.state_dir) / 'creation').exists()


async def test_cancelled_identity_write_rolls_back_and_restores_cache(source, monkeypatch):
    db = await source._ensure_db()
    commit = db.commit
    entered = asyncio.Event()
    async def pause_commit():
        entered.set()
        await asyncio.Future()
    monkeypatch.setattr(db, 'commit', pause_commit)
    task = asyncio.create_task(source.update_character_identity('Lin', 'id', appearance_details='cancelled'))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert source.get_character('Lin').identities[0].appearance_details == ''
    monkeypatch.setattr(db, 'commit', commit)
    await source.update_character('Lin', description='next')
    assert (await source.list_characters())[0].identities[0].appearance_details == ''


async def test_repeated_cancel_keeps_lock_until_rollback_finishes(source, monkeypatch):
    db = await source._ensure_db()
    rollback = db.rollback
    entered, repairing, proceed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def pause_commit():
        entered.set()
        await asyncio.Future()
    async def pause_rollback():
        repairing.set()
        await proceed.wait()
        await rollback()
    monkeypatch.setattr(db, 'commit', pause_commit)
    monkeypatch.setattr(db, 'rollback', pause_rollback)
    task = asyncio.create_task(source.update_character('Lin', description='cancelled'))
    await entered.wait()
    task.cancel()
    await repairing.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    acquired = asyncio.Event()
    async def next_owner():
        async with asset_project_lock(source.state_dir):
            acquired.set()
    waiter = asyncio.create_task(next_owner())
    await asyncio.sleep(.03)
    assert not acquired.is_set()
    proceed.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await waiter
    assert source.get_character('Lin').description == 'original'


@pytest.mark.parametrize('operation', ['characters', 'scenes', 'analysis'])
async def test_bulk_repeated_cancel_cannot_rollback_next_transaction(source, monkeypatch, operation):
    if operation == 'analysis':
        await source.start_analysis_run(
            run_id='cancelled-analysis', pipeline_version='test', schema_version=1,
            spine_template='test', source_sha256='test', source_length=0, chunks=[])
    db = await source._ensure_db()
    commit, rollback = db.commit, db.rollback
    entered, repairing, release_first = asyncio.Event(), asyncio.Event(), asyncio.Event()
    rollback_tasks = []

    async def pause_commit():
        entered.set()
        await asyncio.Future()

    async def pause_first_rollback():
        rollback_tasks.append(asyncio.current_task())
        if len(rollback_tasks) == 1:
            repairing.set()
            await release_first.wait()
        await rollback()

    monkeypatch.setattr(db, 'commit', pause_commit)
    monkeypatch.setattr(db, 'rollback', pause_first_rollback)
    if operation == 'characters':
        write = source.add_characters_atomic([NovelCharacter(name='Cancelled')])
    elif operation == 'scenes':
        write = source.add_scenes_atomic([NovelScene(name='Cancelled')])
    else:
        write = source.publish_character_analysis_atomic(
            'cancelled-analysis', [NovelCharacter(name='Cancelled')], {})
    task = asyncio.create_task(write)
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    await asyncio.wait_for(repairing.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)

    # Once the owner returned, a new transaction must not be affected by any
    # cleanup task that survived the cancellation of the previous writer.
    await db.execute("INSERT INTO scenes(name) VALUES ('Next transaction')")
    release_first.set()
    foreign_tasks = {item for item in rollback_tasks if item is not task}
    if foreign_tasks:
        await asyncio.gather(*foreign_tasks, return_exceptions=True)
    await commit()
    assert await source.get_scene_exact('Next transaction') is not None
    assert not foreign_tasks, 'Rollback escaped the source lock owning Task'
