from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import sqlite3

import pytest

from novelvideo.costs.models import CostValue
from novelvideo.costs.store import CostStore, StoreConflictError


def facts(**changes):
    return dict(attempt_id='a', project_id='p', provider='vendor', account_id='acct',
                model='m', media_type='video', occurred_at='2026-01-01T00:00:00Z', **changes)


def test_replay_persistence_and_corrections(tmp_path):
    store = CostStore(tmp_path / 'cost.db')
    store.create_attempt(facts())
    assert store.get_cost('a').status == 'unpriced'
    value = CostValue(status='confirmed', amount_micros=3200000)
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: store.record_cost('a', 'event', value), range(4)))
    assert sum(results) == 1
    assert len(store.list_revisions('a')) == 1
    with pytest.raises(StoreConflictError):
        store.record_cost('a', 'event', CostValue(status='confirmed', amount_micros=1))
    assert not store.record_cost('a', 'late', CostValue(status='estimated', amount_micros=9))
    store.record_cost('a', 'refund', CostValue(status='confirmed', amount_micros=0, reason='refund'))
    assert CostStore(store.path).get_cost('a').amount_micros == 0


def test_identity_and_rollback(tmp_path):
    store = CostStore(tmp_path / 'cost.db')
    a = facts(external_id='external')
    store.create_attempt(a)
    store.create_attempt(a)
    for key, val in [('attempt_id', 'b'), ('account_id', 'other')]:
        candidate = {**a, 'attempt_id': 'b', key: val}
        if key == 'attempt_id':
            with pytest.raises(StoreConflictError):
                store.create_attempt(candidate)
        else:
            store.create_attempt(candidate)
    with pytest.raises(ValueError):
        store.update_attempt('a', {'project_id': 'elsewhere'})
    with pytest.raises(ValueError):
        store.record_cost('a', 'huge', CostValue(status='estimated', amount_micros=2**63))
    with pytest.raises(ValueError):
        store.record_cost('a', 'secret', CostValue(status='estimated', amount_micros=1), evidence={'prompt': 'secret'})
    assert len(store.list_attempts('p')) == 2
    assert store.list_revisions('a') == []


def test_late_estimate_retains_current_evidence(tmp_path):
    store = CostStore(tmp_path / 'cost.db')
    store.create_attempt(facts())
    store.record_cost('a', 'confirmed', CostValue(status='confirmed', amount_micros=3), evidence={'source': 'billing'})
    assert not store.record_cost('a', 'estimate', CostValue(status='estimated', amount_micros=8))
    assert store.get_cost_record('a')['evidence'] == {'source': 'billing'}
    assert store.get_cost_record('a')['event_id'] == 'confirmed'
    assert store.list_revisions('a')[-1]['applied'] is False
    assert store.snapshot('other')['cost_details'] == {}


def test_invalid_subscription_and_stopping_rule_roll_back(tmp_path):
    store = CostStore(tmp_path / 'cost.db')
    subscription = dict(id='s', provider='v', account_id='acct', starts_at='2026-01-01T00:00:00Z')
    for changes in ({'ends_at': subscription['starts_at']}, {'amount_micros': -1}, {'amount_micros': 2**63}, {'cny_rate': '0'}, {'api_key': 'secret'}):
        with pytest.raises(ValueError):
            store.save_subscription({**subscription, **changes})
    assert store.list_subscriptions() == []
    rule = store.add_price_rule(dict(id='r', version='1', media_type='image', starts_at='2026-01-01T00:00:00Z', items=[dict(unit='item', unit_price='1')]))
    with pytest.raises(ValueError):
        store.stop_price_rule('r', '1', rule.starts_at)
    assert store.list_price_rules()[0] == rule
    store.stop_price_rule('r', '1', '2026-02-01T00:00:00Z')
    with pytest.raises(ValueError):
        store.stop_price_rule('r', '1', '2026-03-01T00:00:00Z')
def test_rule_stop_preserves_revision_snapshot_and_config(tmp_path):
    store = CostStore(tmp_path / 'cost.db')
    rule = dict(id='r', version='1', provider='vendor', media_type='video',
                starts_at='2025-01-01T00:00:00Z', items=[dict(unit='call', unit_price='1')])
    saved = store.add_price_rule(rule)
    with pytest.raises(ValueError):
        store.add_price_rule({**rule, 'id': 'overlap'})
    with pytest.raises(StoreConflictError):
        store.add_price_rule({**rule, 'currency': 'USD'})
    store.create_attempt(facts())
    store.record_cost('a', 'estimate', CostValue(status='estimated', amount_micros=1000000), saved)
    store.stop_price_rule('r', '1', '2026-02-01T00:00:00Z')
    assert store.list_revisions('a')[0]['rule_snapshot']['ends_at'] is None
    assert store.list_price_rules()[0].ends_at is not None
    store.save_subscription(dict(id='s', provider='vendor', account_id='acct', starts_at='2026-01-01T00:00:00Z'))
    store.set_coverage(dict(project_id='p', provider='vendor', start_at='2026-01-01T00:00:00Z', complete=False, gaps=['older history unavailable']))
    snapshot = CostStore(store.path).snapshot('p')
    assert snapshot['subscriptions'][0].amount_micros is None
    assert snapshot['coverage'][0].gaps == ('older history unavailable',)
    assert snapshot['costs']['a'].amount_micros == 1000000
    assert len(snapshot['attempts']) == 1


def test_retry_attempts_and_external_lookup(tmp_path):
    store = CostStore(tmp_path / 'cost.db')
    for identifier in ('a', 'b'):
        store.create_attempt({**facts(), 'attempt_id': identifier, 'external_id': identifier})
        store.record_cost(identifier, 'event', CostValue(status='confirmed', amount_micros=2))
    assert sum(v.amount_micros for v in store.snapshot('p')['costs'].values()) == 4
    assert store.find_by_external('vendor', 'acct', 'b').attempt_id == 'b'
    assert store.find_by_external('vendor', 'other', 'b') is None
    updated = store.update_attempt('a', {'execution_status': 'failed'})
    assert updated.execution_status == 'failed'
    with pytest.raises(StoreConflictError):
        store.create_attempt({**facts(), 'attempt_id': 'c', 'external_id': 'b'})
    assert len(store.list_attempts('p')) == 2


def test_revision_audit_time_is_persisted(tmp_path):
    store = CostStore(tmp_path / 'cost.db')
    store.create_attempt(facts())
    store.record_cost('a', 'event', CostValue(status='confirmed', amount_micros=3))
    recorded_at = store.list_revisions('a')[0]['recorded_at']
    assert datetime.fromisoformat(recorded_at).utcoffset().total_seconds() == 0
    reopened = CostStore(store.path)
    assert reopened.get_cost_record('a')['recorded_at'] == recorded_at
    assert reopened.snapshot('p')['cost_details']['a']['recorded_at'] == recorded_at


@pytest.mark.parametrize('ends_at', [None, datetime(2026, 2, 1), '2026-02-01T00:00:00'])
def test_stop_requires_aware_nonnull_timestamp(tmp_path, ends_at):
    store = CostStore(tmp_path / 'cost.db')
    original = store.add_price_rule(dict(id='r', version='1', media_type='image', starts_at='2026-01-01T00:00:00Z', items=[dict(unit='item', unit_price='1')]))
    with pytest.raises(ValueError):
        store.stop_price_rule('r', '1', ends_at)
    assert store.list_price_rules() == [original]


def test_migrates_legacy_revision_table_without_fabricating_time(tmp_path):
    path = tmp_path / 'old.db'
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE cost_revisions (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id TEXT NOT NULL,
            event_id TEXT NOT NULL, amount_micros INTEGER, applied INTEGER NOT NULL,
            body_json TEXT NOT NULL, UNIQUE(attempt_id,event_id))''')
        db.execute('INSERT INTO cost_revisions VALUES (1, ?, ?, 1, 1, ?)',
                   ('legacy', 'event', '{"value":{"status":"confirmed","amount_micros":1},"evidence":{},"rule_snapshot":null}'))
    store = CostStore(path)
    assert store.list_revisions('legacy')[0]['recorded_at'] is None
    store.create_attempt(facts())
    store.record_cost('a', 'new', CostValue(status='estimated', amount_micros=2))
    assert store.list_revisions('a')[0]['recorded_at'] is not None


def test_attempt_attribution_cannot_change_after_charge(tmp_path):
    store = CostStore(tmp_path / 'cost.db')
    original = store.create_attempt({**facts(), 'workflow': 'flow', 'specifications': [('quality', 'high')]})
    store.record_cost('a', 'charged', CostValue(status='confirmed', amount_micros=10))
    revision = store.list_revisions('a')
    for changes in ({'model': 'other'}, {'media_type': 'image'}, {'workflow': None}, {'specifications': []}):
        with pytest.raises(ValueError):
            store.update_attempt('a', changes)
        assert store.get_attempt('a') == original
        assert store.list_revisions('a') == revision
        assert store.get_cost('a').amount_micros == 10
    enriched = store.update_attempt('a', {'task_id': 'task', 'resource_id': 'resource'})
    assert enriched.task_id == 'task'
    assert store.update_attempt('a', {'task_id': 'task', 'resource_id': 'resource'}) == enriched
    for field in ('task_id', 'resource_id'):
        for value in (None, 'replacement'):
            with pytest.raises(ValueError):
                store.update_attempt('a', {field: value})
            assert store.get_attempt('a') == enriched
    assert store.list_revisions('a') == revision


def test_revision_usage_snapshot_survives_enrichment_and_replay(tmp_path):
    store = CostStore(tmp_path / 'cost.db')
    store.create_attempt({**facts(), 'usage': {'second': '5'}, 'usage_source': 'request'})
    estimate = CostValue(status='estimated', amount_micros=5)
    store.record_cost('a', 'estimate', estimate)
    store.update_attempt('a', {'usage': {'second': '7'}, 'usage_source': 'provider'})
    assert not store.record_cost('a', 'estimate', estimate)
    store.record_cost('a', 'actual', CostValue(status='confirmed', amount_micros=7))
    revisions = store.list_revisions('a')
    assert len(revisions) == 2
    assert revisions[0]['usage'] == {'second': '5'}
    assert revisions[0]['usage_source'] == 'request'
    assert revisions[1]['usage'] == {'second': '7'}
    assert store.get_cost_record('a')['usage_source'] == 'provider'
    assert store.snapshot('p')['cost_details']['a']['usage'] == {'second': '7'}
