from datetime import datetime
import json

import pytest

from novelvideo.costs.queries import CostQueries
from novelvideo.costs.store import CostStore

START = '2026-01-01T00:00:00+08:00'
NOW = '2026-01-03T12:00:00+08:00'


@pytest.fixture
def ledger(tmp_path):
    store = CostStore(tmp_path / 'cost.db')
    store.set_coverage(dict(project_id='p', provider='v', start_at=START, complete=True))
    return store


def add(store, id, status='confirmed', micros=5000, **changes):
    store.create_attempt(dict(attempt_id=id, project_id=changes.pop('project_id', 'p'), provider='v',
                              account_id='a', model='m', media_type='video',
                              occurred_at=changes.pop('occurred_at', START),
                              submission_status=changes.pop('submission_status', 'submitted'), **changes))
    store.record_cost(id, 'first', dict(status=status, amount_micros=micros,
                                      reason='explicit zero' if micros == 0 else None))


def snap(store):
    return CostQueries(store).snapshot('p', now=NOW, created_at=START)


def test_rounding_timezone_and_covered_zeros(ledger):
    add(ledger, 'a')
    add(ledger, 'b', occurred_at='2026-01-01T16:00:00Z')
    result = snap(ledger)
    assert result['summary']['total_cents'] == 2
    assert [p['total_cents'] for p in result['trend']['daily']] == [1, 1, 0]
    assert result['trend']['cumulative'][-1]['total_cents'] == 2
    assert result['breakdown']['channels'][0]['total_cents'] == 2
    assert result['summary']['complete']
    json.dumps(result)


def test_display_states_pending_unknown_and_failed(ledger):
    assert snap(ledger)['summary']['display_state'] == 'empty'
    add(ledger, 'zero', micros=0, execution_status='failed')
    assert snap(ledger)['summary']['display_state'] == 'priced'
    add(ledger, 'pending', submission_status='pending')
    add(ledger, 'unknown', submission_status='unknown')
    add(ledger, 'failed', submission_status='failed')
    result = snap(ledger)['summary']
    assert result['total_cents'] == 0
    assert result['pending_count'] == 1
    assert result['unpriced_count'] == 1
    assert not result['complete']


def test_subscription_isolation_and_unpriced_state(ledger):
    add(ledger, 'sub', 'subscription_covered', None)
    for account in ('a', 'unrelated'):
        ledger.save_subscription(dict(id=account, provider='v', account_id=account,
                                      starts_at=START, amount_micros=9000000))
    result = snap(ledger)
    assert result['summary']['display_state'] == 'subscription_only'
    assert result['summary']['total_cents'] == 0
    assert [s['id'] for s in result['subscriptions']] == ['a']
    assert result['subscriptions'][0]['project_call_count'] == 1
    add(ledger, 'unknown', 'unpriced', None)
    assert snap(ledger)['summary']['display_state'] == 'unpriced_only'


def test_gaps_and_missing_coverage(ledger):
    ledger.set_coverage(dict(project_id='p', provider='v', start_at='2026-01-02T00:00:00+08:00', complete=True))
    result = snap(ledger)
    assert [p['total_cents'] for p in result['trend']['daily']] == [None, 0, 0]
    assert not result['summary']['complete']
    assert result['trend']['cumulative'][-1]['total_cents'] == 0
    assert not CostQueries(ledger).snapshot('other', NOW, START)['summary']['complete']
    ledger.set_coverage(dict(project_id='p', provider='v', start_at=START, complete=False, gaps=['unknown history']))
    add(ledger, 'a')
    assert [p['total_cents'] for p in snap(ledger)['trend']['daily']] == [1, None, None]


def test_actual_refund_original_date_and_detail_isolation(ledger):
    add(ledger, 'a', 'estimated', 1000000)
    ledger.record_cost('a', 'actual', dict(status='confirmed', amount_micros=2000000))
    assert snap(ledger)['trend']['daily'][0]['total_cents'] == 200
    ledger.record_cost('a', 'refund', dict(status='confirmed', amount_micros=0, reason='refund'))
    assert snap(ledger)['trend']['daily'][0]['total_cents'] == 0
    queries = CostQueries(ledger)
    assert len(queries.entry_detail('p', 'a')['revisions']) == 3
    with pytest.raises(KeyError):
        queries.entry_detail('other', 'a')


def test_cursor_stability_filters_and_project_scope(ledger):
    for id in ('a', 'b', 'c'):
        add(ledger, id)
    add(ledger, 'foreign', project_id='other')
    queries = CostQueries(ledger)
    first = queries.entries('p', channel='v', media='video', status='confirmed', limit=2)
    assert [e['attempt_id'] for e in first['entries']] == ['c', 'b']
    second = queries.entries('p', channel='v', media='video', status='confirmed', limit=2, cursor=first['next_cursor'])
    assert [e['attempt_id'] for e in second['entries']] == ['a']
    assert second['next_cursor'] is None
    for cursor in ('bad', first['next_cursor']):
        with pytest.raises(ValueError):
            queries.entries('other', cursor=cursor)
    for limit in (0, -1, True, 501):
        with pytest.raises(ValueError):
            queries.entries('p', limit=limit)
