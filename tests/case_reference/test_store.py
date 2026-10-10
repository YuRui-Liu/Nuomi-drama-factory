import pytest

from novelvideo.case_reference.store import ProposalStore


def test_idempotency_conflict_and_owner_isolation(tmp_path):
    store = ProposalStore(tmp_path)
    first, created = store.create('alice', 'request', {'story': '甲'}, {})
    second, again = store.create('alice', 'request', {'story': '甲'}, {})
    assert created and not again and first['id'] == second['id']
    with pytest.raises(ValueError, match='request'):
        store.create('alice', 'request', {'story': '乙'}, {})
    with pytest.raises(KeyError):
        store.get(first['id'], 'bob')
    assert ProposalStore(tmp_path).get(first['id'], 'alice') == first


def test_cancel_wins_over_late_success(tmp_path):
    store = ProposalStore(tmp_path)
    item, _ = store.create('alice', 'one', {}, {})
    assert store.transition(item['id'], 'alice', ('queued',), 'running')['status'] == 'running'
    store.transition(item['id'], 'alice', ('queued', 'running'), 'cancelled')
    late = store.transition(item['id'], 'alice', ('running',), 'succeeded', result={'prompt': 'late'})
    assert late['status'] == 'cancelled' and late.get('result') is None


def test_only_one_worker_can_claim(tmp_path):
    store = ProposalStore(tmp_path)
    item, _ = store.create('alice', 'one', {}, {})
    assert store.claim(item['id'], 'alice') is True
    assert store.claim(item['id'], 'alice') is False
