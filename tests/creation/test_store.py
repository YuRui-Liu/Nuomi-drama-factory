from concurrent.futures import ThreadPoolExecutor
import sqlite3
from threading import Barrier

import pytest

from novelvideo.creation.store import ObjectStore, RevisionConflict


def test_versions_persist_after_reopen(tmp_path):
    store = ObjectStore(tmp_path)
    first = store.create('character', {'name': 'One'}, actor_id='a')
    second = store.update(first['id'], 1, {'name': 'Two'}, actor_id='b')
    reopened = ObjectStore(tmp_path)
    assert reopened.read(first['id'], 1) == first
    assert reopened.read(first['id']) == second
    assert second['revision'] == 2
    assert second['actor_id'] == 'b'


def test_unknown_kind_and_missing_object(tmp_path):
    store = ObjectStore(tmp_path)
    with pytest.raises(ValueError):
        store.create('unknown', {}, actor_id='a')
    assert store.read('../../outside') is None


def test_concurrent_expected_revision_only_one_wins(tmp_path):
    store = ObjectStore(tmp_path)
    obj = store.create('scene', {}, actor_id='a')
    barrier = Barrier(2)

    def update(value):
        other = ObjectStore(tmp_path)
        barrier.wait()
        try:
            return other.update(obj['id'], 1, {'value': value}, actor_id='a')
        except RevisionConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, [1, 2]))
    assert sum(result is not None for result in results) == 1
    assert store.read(obj['id'])['revision'] == 2
    assert store.read(obj['id'], 1)['payload'] == {}


def test_transaction_rollback_and_foreign_keys(tmp_path):
    store = ObjectStore(tmp_path)
    obj = store.create('media', {}, actor_id='a')
    with pytest.raises(RuntimeError):
        with store.transaction() as conn:
            assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
            conn.execute('UPDATE objects SET current_revision = 99 WHERE id = ?', (obj['id'],))
            raise RuntimeError('abort')
    assert store.read(obj['id'])['revision'] == 1
    with pytest.raises(sqlite3.IntegrityError):
        with store.transaction() as conn:
            conn.execute("INSERT INTO object_versions VALUES ('missing', 1, '{}', 'a', 'now')")


def test_failed_version_insert_rolls_back_current_revision(tmp_path):
    store = ObjectStore(tmp_path)
    obj = store.create('style', {}, actor_id='a')
    with store.transaction() as conn:
        conn.execute("CREATE TRIGGER fail_version BEFORE INSERT ON object_versions BEGIN SELECT RAISE(ABORT, 'fail'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.update(obj['id'], 1, {'name': 'new'}, actor_id='b')
    assert store.read(obj['id']) == obj


def test_pending_proposal_can_have_no_decision_actor(tmp_path):
    store = ObjectStore(tmp_path)
    with store.transaction() as conn:
        conn.execute(
            'INSERT INTO proposals (id, scope, input_hash, authorized_operations) VALUES (?, ?, ?, ?)',
            ('proposal-1', '{}', 'hash', '[]'),
        )
        row = conn.execute('SELECT decision, actor_id FROM proposals WHERE id = ?', ('proposal-1',)).fetchone()
        assert row['decision'] is None
        assert row['actor_id'] is None
