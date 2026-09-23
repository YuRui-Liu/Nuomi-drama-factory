import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import datetime, timezone, timedelta

import pytest

from novelvideo.costs.models import CostAttempt, CostValue, PriceRule
from novelvideo.costs.store import CostStore, StoreConflictError
from novelvideo.costs.service import CostService, Observation, ActualEvidence
from novelvideo.costs.context import CostContext, cost_context, resolve_cost_context

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)

def attempt(**kw):
    return CostAttempt(attempt_id='a', project_id='p', provider='provider', account_id='acct',
                       model='m', media_type='image', occurred_at=NOW, **kw)

@pytest.fixture
def service(tmp_path):
    return CostService(CostStore(tmp_path / 'costs.db'))

def rule():
    return PriceRule(id='r', version='1', media_type='image', starts_at=NOW,
                     items=[dict(unit='item', unit_price='2')])

def test_prepare_before_send_and_recovery(service):
    sent = []
    def send(facts):
        prepared = service.prepare(facts)
        assert service.store.get_attempt(prepared.attempt_id).submission_status == 'pending'
        sent.append('external')
    with pytest.raises(ValueError):
        send({'project_id': ''})
    assert not sent
    send(attempt())
    service.mark_submission_unknown('a')
    service.submitted('a', 'external')
    restored = CostService(service.store).find_by_external('provider', 'acct', 'external')
    service.observe(restored.attempt_id, 'done', Observation(execution_status='succeeded'))
    assert sent == ['external']

def test_snapshot_replay_and_no_downgrade(service):
    service.store.add_price_rule(rule())
    service.prepare(attempt())
    service.submitted('a', 'external')
    facts = Observation(execution_status='succeeded', usage={'item': '1'}, usage_source='provider')
    service.observe('a', 'poll1', facts)
    service.store.stop_price_rule('r', '1', NOW + timedelta(seconds=1))
    service.store.add_price_rule(PriceRule(id='specific', version='1', provider='provider', media_type='image',
                                         starts_at=NOW, items=[dict(unit='item', unit_price='99')]))
    service.observe('a', 'poll2', Observation(usage={'item': '2'}, usage_source='provider'))
    assert service.store.get_cost('a').amount_micros == 4_000_000
    service.observe('a', 'poll1', facts)
    assert service.store.get_attempt('a').usage == {'item': '2'}
    with pytest.raises(StoreConflictError):
        service.observe('a', 'poll1', Observation(usage={'item': '9'}))
    service.observe('a', 'actual', Observation(actual_cost=CostValue(status='confirmed', amount_micros=3_000_000),
                    evidence=ActualEvidence(source='billing', original_amount='3', original_currency='CNY')))
    service.observe('a', 'late', Observation(usage={'item': '8'}))
    assert service.store.get_cost('a').amount_micros == 3_000_000

def test_subscription_and_extra_charge(service):
    service.store.save_subscription(dict(id='s', provider='provider', account_id='acct', starts_at=NOW,
                                         ends_at=NOW + timedelta(days=1)))
    service.prepare(attempt())
    service.submitted('a', 'external')
    service.observe('a', 'poll', Observation(execution_status='succeeded'))
    assert service.store.get_cost('a').status == 'subscription_covered'
    service.observe('a', 'bill', Observation(actual_cost=CostValue(status='confirmed', amount_micros=1_000_000),
                    evidence=ActualEvidence(source='billing', original_amount='1', original_currency='CNY')))
    assert service.store.get_cost('a').status == 'confirmed'

def test_missing_evidence_and_failed_no_usage(service):
    service.store.add_price_rule(rule())
    service.prepare(attempt())
    service.submitted('a', 'external')
    service.observe('a', 'failed', Observation(execution_status='failed'))
    assert service.store.get_cost('a').status == 'unpriced'
    service.observe('a', 'foreign', Observation(evidence=ActualEvidence(source='billing', original_amount='1', original_currency='USD')))
    assert service.store.get_cost('a').status == 'unpriced'
    with pytest.raises(ValueError):
        Observation(actual_cost=CostValue(status='confirmed', amount_micros=1))

def test_context_async_isolation_and_reset():
    async def run():
        async def child(project):
            with cost_context(CostContext(project_id=project, media_type='image')):
                await asyncio.sleep(0)
                return resolve_cost_context().project_id
        assert await asyncio.gather(child('a'), child('b')) == ['a', 'b']
    asyncio.run(run())
    with pytest.raises(RuntimeError):
        with cost_context(CostContext(project_id='p', media_type='video')):
            raise RuntimeError()
    assert resolve_cost_context() is None

def test_observation_validation_failure_rolls_back_usage_and_event(service):
    service.prepare(attempt())
    service.submitted('a', 'external')
    with pytest.raises(ValueError, match='disagrees'):
        service.observe('a', 'bill', Observation(usage={'item': '5'}, actual_cost=CostValue(status='confirmed', amount_micros=1),
                        evidence=ActualEvidence(source='billing', original_amount='2', original_currency='CNY')))
    assert service.store.get_attempt('a').usage == {}
    assert service.store.list_revisions('a') == []
    service.observe('a', 'bill', Observation(usage={'item': '2'}))
    assert service.store.get_attempt('a').usage == {'item': '2'}

@pytest.mark.parametrize('account,ends', [('other', NOW + timedelta(days=1)), ('acct', NOW)])
def test_subscription_scope_and_exclusive_end(service, account, ends):
    service.store.save_subscription(dict(id='s', provider='provider', account_id=account,
        starts_at=NOW-timedelta(days=1), ends_at=ends))
    service.prepare(attempt())
    service.submitted('a', 'external')
    assert service.observe('a', 'poll', Observation()).status == 'unpriced'

def test_no_implicit_call_quantity_or_request_price_on_failure(service):
    service.store.add_price_rule(PriceRule(id='call', version='1', media_type='image', starts_at=NOW,
                                         items=[dict(unit='call', unit_price='2')]))
    service.prepare(attempt())
    service.submitted('a', 'external')
    assert service.observe('a', 'empty', Observation(execution_status='succeeded')).status == 'unpriced'
    assert service.observe('a', 'fail', Observation(execution_status='failed', usage={'call': '1'}, usage_source='request')).status == 'unpriced'
    assert service.observe('a', 'measured', Observation(usage={'call': '1'}, usage_source='provider')).amount_micros == 2_000_000

def test_failed_before_send_excluded(service):
    service.prepare(attempt(usage={'item': '1'}, usage_source='request'))
    service.failed_before_send('a')
    assert service.observe('a', 'recheck', Observation()).status == 'unpriced'
    assert service.store.get_attempt('a').submission_status == 'failed'

def test_late_lifecycle_events_do_not_regress_terminal_state(service):
    service.prepare(attempt())
    service.submitted('a', 'external')
    service.observe('a', 'done', Observation(execution_status='succeeded'))
    service.submitted('a', 'external')
    service.mark_submission_unknown('a')
    service.observe('a', 'late', Observation(execution_status='running'))
    assert service.store.get_attempt('a').execution_status == 'succeeded'
    assert service.store.get_attempt('a').submission_status == 'submitted'

def test_context_rejects_sensitive_payload_fields():
    with pytest.raises(ValueError):
        CostContext(project_id='p', media_type='image', specifications=(('prompt', 'secret'),))

def test_provider_usage_does_not_promote_request_quantities(service):
    service.store.add_price_rule(rule())
    service.prepare(attempt(usage={'item': '10'}, usage_source='request'))
    service.submitted('a', 'external')
    value = service.observe('a', 'failed', Observation(execution_status='failed', usage={'credit': '0'}, usage_source='provider'))
    assert value.status == 'unpriced'
    assert service.store.get_attempt('a').usage == {'credit': '0'}

def test_original_evidence_derives_confirmed_cost(service):
    service.prepare(attempt())
    service.submitted('a', 'external')
    value = service.observe('a', 'bill', Observation(evidence=ActualEvidence(source='billing', original_amount='2', original_currency='USD', cny_rate='7')))
    assert value == CostValue(status='confirmed', amount_micros=14_000_000)
    assert service.store.list_revisions('a')[0]['evidence']['original_amount'] == '2'

def test_failed_before_send_races_submission_atomically(service):
    service.prepare(attempt())
    gate = Barrier(2)
    other = CostService(CostStore(service.store.path))
    def update(send):
        gate.wait()
        try:
            return other.submitted('a', 'external') if send else service.failed_before_send('a')
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, [True, False]))
    assert sum(result is not None for result in results) == 1
    stored = service.store.get_attempt('a')
    assert (stored.submission_status, stored.external_id) in [('submitted', 'external'), ('failed', None)]
