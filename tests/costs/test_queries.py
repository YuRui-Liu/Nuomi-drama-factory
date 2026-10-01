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


def test_native_credits_only_sum_measured_submitted_project_usage(ledger):
    add(ledger, 'actual', 'unpriced', None, usage={'credit': '1.25'}, usage_source='provider')
    add(ledger, 'actual2', 'unpriced', None, usage={'credit': '0.15'}, usage_source='provider')
    add(ledger, 'request', 'unpriced', None, usage={'credit': '99'}, usage_source='request')
    add(ledger, 'foreign', 'unpriced', None, project_id='other', usage={'credit': '99'}, usage_source='provider')
    add(ledger, 'unsent', 'unpriced', None, submission_status='pending', usage={'credit': '99'}, usage_source='provider')
    result = snap(ledger)
    assert result['measured_credits'] == [{'provider': 'v', 'account_id': 'a', 'credit': '1.40'}]
    assert result['summary']['priced_count'] == 0

def test_priced_count_distinguishes_confirmed_zero_from_unsent(ledger):
    add(ledger, 'unsent', micros=0, submission_status='failed')
    assert snap(ledger)['summary']['priced_count'] == 0
    add(ledger, 'zero', micros=0)
    result = snap(ledger)
    assert result['summary']['priced_count'] == 1
    assert result['breakdown']['channels'][0]['priced_count'] == 1
    assert result['breakdown']['media'][0]['priced_count'] == 1


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


@pytest.mark.parametrize('submission_status', ['pending', 'failed', 'unknown'])
def test_excluded_lifecycle_amount_is_audit_only(ledger, submission_status):
    add(ledger, 'a', micros=1000000, submission_status=submission_status)
    queries = CostQueries(ledger)
    assert snap(ledger)['summary']['total_cents'] == 0
    entry = queries.entries('p')['entries'][0]
    assert entry['amount_cents'] is None
    assert entry['value']['amount_micros'] == 1000000
    detail = queries.entry_detail('p', 'a')
    assert detail['current_cost']['amount_cents'] is None
    assert detail['current_cost']['value']['amount_micros'] == 1000000


def test_detail_keeps_current_and_revisions_in_one_read_transaction(ledger, monkeypatch):
    add(ledger, 'a', micros=1000000)
    original_connection = ledger._connection
    from contextlib import contextmanager

    @contextmanager
    def interleave_after_read(*, write=False):
        with original_connection(write=write) as db:
            yield db
        if not write:
            other = CostStore(ledger.path)
            other.record_cost('a', 'later', dict(status='confirmed', amount_micros=2000000))

    monkeypatch.setattr(ledger, '_connection', interleave_after_read)
    detail = CostQueries(ledger).entry_detail('p', 'a')
    applied = [revision for revision in detail['revisions'] if revision['applied']]
    assert detail['current_cost']['value'] == applied[-1]['value']
    assert detail['current_cost']['amount_cents'] == 100


def test_runninghub_is_settled_in_credits_without_currency_conversion(ledger):
    for identity, usage in [('rh-paid', {'credit': '56'}), ('rh-zero', {'credit': '0'}), ('rh-unknown', {})]:
        ledger.create_attempt(dict(attempt_id=identity, project_id='p', provider='runninghub',
            account_id='rh', model='workflow', media_type='video', occurred_at=START,
            submission_status='submitted', usage_source='provider', usage=usage))
    add(ledger, 'cash', micros=1000000)
    query = CostQueries(ledger)
    result = snap(ledger)
    assert result['summary']['unpriced_count'] == 1
    assert result['summary']['native_credit_count'] == 2
    assert result['summary']['native_credit_total'] == '56'
    assert result['summary']['total_cents'] == 100
    assert result['summary']['cny_attempt_count'] == 1
    assert {r['attempt_id'] for r in query.entries('p', status='unpriced')['entries']} == {'rh-unknown'}
    known = query.entries('p', channel='runninghub', status='confirmed')['entries']
    assert len(known) == 2
    assert all(r['billing']['unit'] == 'RH_CREDIT' and r['amount_cents'] is None for r in known)
    assert query.entry_detail('p', 'rh-paid')['current_cost']['billing'] == dict(unit='RH_CREDIT', amount='56', status='confirmed')
