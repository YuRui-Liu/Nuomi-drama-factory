from datetime import datetime, timezone, timedelta
import pytest
from novelvideo.costs.store import CostStore, StoreConflictError
from novelvideo.costs.models import CostAttempt, CostValue, PriceRule

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)

@pytest.fixture
def store(tmp_path):
    s = CostStore(tmp_path / 'cost.db')
    s.create_attempt(CostAttempt(attempt_id='a', project_id='p', provider='x', account_id='x', model='m', media_type='image', occurred_at=NOW, submission_status='submitted', execution_status='succeeded', usage={'item':'2'}, usage_source='provider'))
    s.add_price_rule(PriceRule(id='r', version='1', media_type='image', starts_at=NOW, items=[{'unit':'item','unit_price':'2'}]))
    return s

def service(store):
    from novelvideo.costs.reprice import RepriceService
    return RepriceService(store)

def test_durable_idempotent_preview(store):
    preview = service(store).preview('p')
    assert preview['total_cents'] == 400
    assert preview['affected_count'] == 1
    restored = service(CostStore(store.path))
    result = restored.apply('p', preview['preview_id'])
    assert restored.apply('p', preview['preview_id']) == result
    assert store.get_cost('a').amount_micros == 4000000
    assert len(store.list_revisions('a')) == 1
    assert service(store).preview('p')['affected_count'] == 0

@pytest.mark.parametrize('change', ['confirmed', 'usage', 'rule', 'revision'])
def test_stale_rejects_atomically(store, change):
    preview = service(store).preview('p')
    if change == 'confirmed':
        store.record_cost('a','actual',CostValue(status='confirmed',amount_micros=100))
    elif change == 'usage':
        store.update_attempt('a', {'usage': {'item':'3'}})
    elif change == 'rule':
        store.stop_price_rule('r','1', NOW + timedelta(seconds=1))
    else:
        store.record_cost('a','later',CostValue(status='unpriced'))
    with pytest.raises(StoreConflictError):
        service(store).apply('p',preview['preview_id'])
    assert store.get_cost('a').status != 'estimated'

def test_foreign_and_failed_request(store):
    preview = service(store).preview('p')
    with pytest.raises(KeyError):
        service(store).apply('foreign',preview['preview_id'])
    store.update_attempt('a', {'execution_status':'failed','usage_source':'request'})
    assert service(store).preview('p')['affected_count'] == 0

def test_stale_second_row_does_not_partially_apply(store):
    other = store.get_attempt('a').model_copy(update={'attempt_id':'b'})
    store.create_attempt(other)
    preview = service(store).preview('p')
    store.update_attempt('b', {'usage': {'item':'9'}})
    with pytest.raises(StoreConflictError):
        service(store).apply('p', preview['preview_id'])
    assert store.get_cost('a').status == 'unpriced'
    assert store.list_revisions('a') == []

def test_new_more_specific_rule_invalidates_preview(store):
    preview = service(store).preview('p')
    store.add_price_rule(PriceRule(id='specific',version='1',provider='x',media_type='image',starts_at=NOW,items=[{'unit':'item','unit_price':'8'}]))
    with pytest.raises(StoreConflictError):
        service(store).apply('p',preview['preview_id'])
