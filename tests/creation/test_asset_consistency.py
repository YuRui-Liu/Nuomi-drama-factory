import asyncio
import json

import pytest

from novelvideo.creation.asset_adapter import AssetAdapter
from novelvideo.creation.store import ObjectStore, RevisionConflict
from novelvideo.models import NovelCharacter
from novelvideo.sqlite_store import SQLiteStore


@pytest.fixture
async def registered(tmp_path):
    source = SQLiteStore('test', output_dir=str(tmp_path))
    await source.initialize()
    await source.add_character(NovelCharacter(name='Lin', age_group='youth'))
    adapter = AssetAdapter(source, ObjectStore(tmp_path))
    base = await adapter.register_base('character', 'Lin')
    yield source, adapter, base
    await source.close()


def intent(adapter, base):
    before = adapter.source.get_character('Lin').model_dump(mode='json')
    with adapter.objects.transaction() as conn:
        conn.execute('INSERT INTO asset_publication_intents VALUES (?,?,?,?)',
                     (base.base_asset_id, 1, json.dumps({'age_group': 'youth'}),
                      json.dumps({'age_group': 'child'})))
        conn.execute('INSERT INTO asset_publication_evidence VALUES (?,?,?)',
                     (base.base_asset_id, json.dumps(before), json.dumps({**before, 'age_group': 'child'})))


@pytest.mark.parametrize('source_age,revision', [('youth', 1), ('child', 2)])
async def test_reconcile_interrupted_publication_after_restart(registered, source_age, revision):
    source, adapter, base = registered
    intent(adapter, base)
    await source.update_character('Lin', age_group=source_age)
    restarted = AssetAdapter(source, ObjectStore(source.state_dir))
    result = await restarted.reconcile_publication(base.base_asset_id)
    assert result.revision == revision
    assert (await restarted.resolve(result))['payload']['age_group'] == source_age
    assert (await restarted.resolve(base))['payload']['age_group'] == 'youth'
    assert await restarted.reconcile_publication(base.base_asset_id) == result
    with adapter.objects.transaction() as conn:
        assert not conn.execute('SELECT * FROM asset_publication_intents').fetchall()


async def test_reconciliation_rejects_ambiguous_source_drift(registered):
    source, adapter, base = registered
    intent(adapter, base)
    await source.update_character('Lin', age_group='elder')
    with pytest.raises(RuntimeError, match='drift'):
        await adapter.reconcile_publication(base.base_asset_id)
    assert source.get_character('Lin').age_group == 'elder'
    with adapter.objects.transaction() as conn:
        assert conn.execute('SELECT * FROM asset_publication_intents').fetchone()


async def test_reconciliation_does_not_overwrite_advanced_snapshot(registered):
    source, adapter, base = registered
    intent(adapter, base)
    payload = (await adapter.resolve(base))['payload']
    adapter.objects.update(base.base_asset_id, 1, {**payload, 'age_group': 'elder'}, 'other')
    await source.update_character('Lin', age_group='child')
    with pytest.raises(RevisionConflict):
        await adapter.reconcile_publication(base.base_asset_id)
    assert adapter.objects.read(base.base_asset_id)['payload']['age_group'] == 'elder'


async def test_task_lock_child_cannot_inherit_ownership(tmp_path):
    from novelvideo.production_workflow.coordination import asset_project_lock
    entered = asyncio.Event()
    async def child():
        async with asset_project_lock(tmp_path):
            entered.set()
    async with asset_project_lock(tmp_path):
        async with asset_project_lock(tmp_path):
            task = asyncio.create_task(child())
            await asyncio.sleep(0.06)
            assert not entered.is_set()
    await asyncio.wait_for(task, 2)
    assert entered.is_set()


async def test_sync_workflow_lock_reuses_only_owning_task(tmp_path):
    from novelvideo.production_workflow.coordination import asset_project_lock
    from novelvideo.production_workflow import production_workflow_project_lock
    async with asset_project_lock(tmp_path):
        with production_workflow_project_lock(tmp_path):
            pass
        async def other():
            with pytest.raises(RuntimeError, match='asynchronous'):
                with production_workflow_project_lock(tmp_path):
                    pass
        await asyncio.create_task(other())


async def test_legacy_partial_intent_requires_manual_reconciliation(registered):
    source, adapter, base = registered
    intent(adapter, base)
    with adapter.objects.transaction() as conn:
        conn.execute('DELETE FROM asset_publication_evidence')
    await source.update_character('Lin', age_group='child')
    with pytest.raises(RuntimeError, match='complete source evidence'):
        await adapter.reconcile_publication(base.base_asset_id)
    assert adapter.objects.read(base.base_asset_id)['revision'] == 1


async def test_reconciliation_rejects_drift_outside_requested_fields(registered):
    source, adapter, base = registered
    intent(adapter, base)
    await source.update_character('Lin', age_group='child', reference_audio_path='later.wav')
    with pytest.raises(RuntimeError, match='drift'):
        await adapter.reconcile_publication(base.base_asset_id)
    assert source.get_character('Lin').reference_audio_path == 'later.wav'


async def test_reconciliation_recognizes_committed_snapshot_before_journal_cleanup(registered):
    source, adapter, base = registered
    intent(adapter, base)
    await source.update_character('Lin', age_group='child')
    adapter.objects.update(base.base_asset_id, 1, source.get_character('Lin').model_dump(mode='json'), 'asset-adapter')
    assert (await adapter.reconcile_publication(base.base_asset_id)).revision == 2
    assert adapter.objects.read(base.base_asset_id)['revision'] == 2


async def test_real_failed_compensation_can_be_reconciled_after_restart(registered, monkeypatch):
    source, adapter, base = registered
    write = source.update_character
    calls = 0
    async def fail_compensation(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError('compensation interrupted')
        await write(*args, **kwargs)
    def fail_snapshot(*args, **kwargs):
        raise RuntimeError('snapshot interrupted')
    monkeypatch.setattr(source, 'update_character', fail_compensation)
    monkeypatch.setattr(adapter.objects, 'update', fail_snapshot)
    with pytest.raises(RuntimeError, match='compensation interrupted'):
        await adapter.publish_revision(base.base_asset_id, None, 1, {'age_group': 'child'})
    restarted = AssetAdapter(source, ObjectStore(source.state_dir))
    assert (await restarted.reconcile_publication(base.base_asset_id)).revision == 2


async def test_lock_cancellation_releases_owner_and_child_waiter(tmp_path):
    from novelvideo.production_workflow.coordination import asset_project_lock
    started = asyncio.Event()
    async def owner():
        async with asset_project_lock(tmp_path):
            started.set()
            await asyncio.Event().wait()
    task = asyncio.create_task(owner())
    await started.wait()
    async def waiter():
        async with asset_project_lock(tmp_path):
            return True
    cancelled = asyncio.create_task(waiter())
    await asyncio.sleep(0.04)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.wait_for(waiter(), 2)


async def test_lock_rejects_reverse_sync_async_order(tmp_path):
    from novelvideo.production_workflow.coordination import asset_project_lock
    from novelvideo.production_workflow import production_workflow_project_lock
    with production_workflow_project_lock(tmp_path):
        with pytest.raises(RuntimeError, match='before'):
            async with asyncio.timeout(0.2):
                async with asset_project_lock(tmp_path):
                    pass


async def test_async_lock_waits_for_existing_cross_process_workflow_lock(tmp_path):
    import sys
    from novelvideo.production_workflow.coordination import asset_project_lock
    child = await asyncio.create_subprocess_exec(
        sys.executable, '-c',
        'import portalocker,sys; f=open(sys.argv[1],"a+b"); portalocker.lock(f,portalocker.LOCK_EX); print("locked",flush=True); sys.stdin.readline()',
        str(tmp_path / '.production_workflow.lock'),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
    entered = asyncio.Event()
    async def waiter():
        async with asset_project_lock(tmp_path):
            entered.set()
    task = None
    try:
        assert await asyncio.wait_for(child.stdout.readline(), 5) == b'locked\n'
        task = asyncio.create_task(waiter())
        await asyncio.sleep(0.06)
        assert not entered.is_set()
        child.stdin.write(b'\n')
        await child.stdin.drain()
        await asyncio.wait_for(child.wait(), 5)
        await asyncio.wait_for(task, 2)
        assert entered.is_set()
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_compensation_stays_in_lock_owning_task(registered, monkeypatch):
    from novelvideo.production_workflow.coordination import asset_project_lock
    source, adapter, base = registered
    write = source.update_character
    async def coordinated(*args, **kwargs):
        async with asset_project_lock(source.state_dir):
            await write(*args, **kwargs)
    def fail_snapshot(*args, **kwargs):
        raise RuntimeError('snapshot interrupted')
    monkeypatch.setattr(source, 'update_character', coordinated)
    monkeypatch.setattr(adapter.objects, 'update', fail_snapshot)
    with pytest.raises(RuntimeError, match='snapshot interrupted'):
        await asyncio.wait_for(adapter.publish_revision(base.base_asset_id, None, 1, {'age_group': 'child'}), 0.3)
    assert source.get_character('Lin').age_group == 'youth'


async def test_reconciliation_after_fresh_source_reopen(registered):
    source, adapter, base = registered
    intent(adapter, base)
    await source.update_character('Lin', age_group='child')
    reopened = SQLiteStore('test', output_dir=source.project_dir, state_dir=source.state_dir)
    await reopened.initialize()
    await reopened.load_graph_state()
    try:
        restarted = AssetAdapter(reopened, ObjectStore(source.state_dir))
        result = await restarted.reconcile_publication(base.base_asset_id)
        assert result.revision == 2
        assert (await restarted.resolve(result))['payload']['age_group'] == 'child'
    finally:
        await reopened.close()


async def test_reconcile_uses_journal_cas_after_source_read(registered, monkeypatch):
    source, adapter, base = registered
    intent(adapter, base)
    await source.update_character('Lin', age_group='child')
    read = adapter._source_record
    async def altered(mapping):
        record = await read(mapping)
        with adapter.objects.transaction() as conn:
            conn.execute('UPDATE asset_publication_intents SET requested=?', ('{}',))
        return record
    monkeypatch.setattr(adapter, '_source_record', altered)
    with pytest.raises(RuntimeError, match='intent changed'):
        await adapter.reconcile_publication(base.base_asset_id)
    assert adapter.objects.read(base.base_asset_id)['revision'] == 1


async def test_cancelled_publication_compensates_and_releases_project_lock(registered, monkeypatch):
    source, adapter, base = registered
    write = source.update_character
    calls = 0
    async def cancel_after_write(*args, **kwargs):
        nonlocal calls
        calls += 1
        await write(*args, **kwargs)
        if calls == 1:
            raise asyncio.CancelledError()
    monkeypatch.setattr(source, 'update_character', cancel_after_write)
    with pytest.raises(asyncio.CancelledError):
        await adapter.publish_revision(base.base_asset_id, None, 1, {'age_group': 'child'})
    assert source.get_character('Lin').age_group == 'youth'
    assert await asyncio.wait_for(adapter.register_base('character', 'Lin'), 2) == base
    with adapter.objects.transaction() as conn:
        assert not conn.execute('SELECT * FROM asset_publication_intents').fetchall()
        assert not conn.execute('SELECT * FROM asset_publication_evidence').fetchall()


async def test_repeated_cancellation_leaves_recoverable_intent(registered, monkeypatch):
    source, adapter, base = registered
    write = source.update_character
    written, compensating = asyncio.Event(), asyncio.Event()
    calls = 0
    async def interrupt(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            await write(*args, **kwargs)
            written.set()
        else:
            compensating.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(source, 'update_character', interrupt)
    task = asyncio.create_task(adapter.publish_revision(base.base_asset_id, None, 1, {'age_group': 'child'}))
    await asyncio.wait_for(written.wait(), 2)
    task.cancel()
    await asyncio.wait_for(compensating.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with adapter.objects.transaction() as conn:
        assert conn.execute('SELECT * FROM asset_publication_intents').fetchone()
        assert conn.execute('SELECT * FROM asset_publication_evidence').fetchone()
    assert (await asyncio.wait_for(adapter.reconcile_publication(base.base_asset_id), 2)).revision == 2


@pytest.mark.parametrize('kind', ['character', 'scene'])
async def test_variant_recovery_preserves_base_and_pinned_history(registered, monkeypatch, kind):
    from novelvideo.models import NovelScene
    source, adapter, char = registered
    if kind == 'scene':
        await source.add_scene(NovelScene(name='Hall'))
        base = await adapter.register_base('scene', 'Hall')
        field, value = 'time_of_day', 'night'
        method = 'update_scene'
    else:
        base = char
        field, value = 'appearance_details', 'coat'
        method = 'update_character_identity'
    variant = await adapter.create_variant(base.base_asset_id, 'test', {}, 'variant-' + kind)
    original = (await adapter.resolve(variant))['payload']
    write = getattr(source, method)
    calls = 0
    async def fail_compensation(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError('compensation interrupted')
        return await write(*args, **kwargs)
    def fail_snapshot(*args, **kwargs):
        raise RuntimeError('snapshot interrupted')
    monkeypatch.setattr(source, method, fail_compensation)
    monkeypatch.setattr(adapter.objects, 'update', fail_snapshot)
    with pytest.raises(RuntimeError, match='compensation interrupted'):
        await adapter.publish_revision(base.base_asset_id, variant.variant_id, 1, {field: value})
    recovered = await adapter.reconcile_publication(base.base_asset_id, variant.variant_id)
    assert recovered.revision == 2
    assert (await adapter.resolve(recovered))['payload'][field] == value
    assert (await adapter.resolve(variant))['payload'] == original
    assert adapter.objects.read(base.base_asset_id)['revision'] == 1


async def test_reconcile_source_read_does_not_hold_snapshot_transaction(registered, monkeypatch):
    import sqlite3
    source, adapter, base = registered
    intent(adapter, base)
    read = adapter._source_record
    async def checked(mapping):
        conn = sqlite3.connect(adapter.objects.db_path, timeout=0)
        try:
            conn.execute('BEGIN IMMEDIATE')
            conn.rollback()
        finally:
            conn.close()
        return await read(mapping)
    monkeypatch.setattr(adapter, '_source_record', checked)
    assert await adapter.reconcile_publication(base.base_asset_id) == base


async def test_reconcile_committed_noop_publication(registered):
    source, adapter, base = registered
    before = source.get_character('Lin').model_dump(mode='json')
    with adapter.objects.transaction() as conn:
        conn.execute('INSERT INTO asset_publication_intents VALUES (?,?,?,?)',
                     (base.base_asset_id, 1, '{}', '{}'))
        conn.execute('INSERT INTO asset_publication_evidence VALUES (?,?,?)',
                     (base.base_asset_id, json.dumps(before), json.dumps(before)))
    adapter.objects.update(base.base_asset_id, 1, before, 'asset-adapter')
    assert (await adapter.reconcile_publication(base.base_asset_id)).revision == 2


async def test_sync_callback_cannot_block_async_owner_loop(tmp_path, monkeypatch):
    import portalocker
    from novelvideo.production_workflow.coordination import asset_project_lock
    from novelvideo.production_workflow import production_workflow_project_lock
    loop = asyncio.get_running_loop()
    result = loop.create_future()
    async with asset_project_lock(tmp_path):
        def would_block(*args, **kwargs):
            raise AssertionError('callback reached blocking OS lock')
        # Detect the blocking call without freezing the entire pytest process.
        monkeypatch.setattr(portalocker, 'lock', would_block)
        def callback():
            assert asyncio.current_task() is None
            try:
                with production_workflow_project_lock(tmp_path):
                    pass
            except BaseException as exc:
                result.set_result(exc)
            else:
                result.set_result(None)
        loop.call_soon(callback)
        error = await asyncio.wait_for(result, 2)
        assert isinstance(error, RuntimeError), repr(error)
        assert 'asynchronous writer' in str(error)
