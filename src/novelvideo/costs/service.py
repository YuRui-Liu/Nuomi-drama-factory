"""Durable lifecycle service. Adapters prepare before sending, then report typed facts."""
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator
from .models import CostAttempt, CostValue, ExecutionStatus, Identity, PriceRule, pricing_decimal
from .pricing import quote, charge_micros
from .store import CostStore
from .context import validate_safe_metadata


class ActualEvidence(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    type: Literal['provider_actual', 'billing_actual'] = 'provider_actual'
    source: Identity
    original_amount: Decimal
    original_currency: Identity
    cny_rate: Decimal | None = None
    reason: Identity | None = None

    @field_validator('original_amount', 'cny_rate', mode='before')
    @classmethod
    def decimal_value(cls, value, info):
        return None if value is None else pricing_decimal(value, positive=info.field_name == 'cny_rate')


class Observation(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    execution_status: ExecutionStatus | None = None
    usage: dict[Identity, StrictStr] = Field(default_factory=dict)
    usage_source: Identity | None = None
    actual_cost: CostValue | None = None
    evidence: ActualEvidence | None = None

    @field_validator('usage')
    @classmethod
    def valid_usage(cls, value):
        validate_safe_metadata(value)
        return CostAttempt.validate_usage(value)

    @model_validator(mode='after')
    def actual_evidence(self):
        if self.actual_cost is not None and (self.actual_cost.status != 'confirmed' or self.evidence is None):
            raise ValueError('actual cost requires confirmed value and provenance')
        if self.evidence is not None and self.actual_cost is None:
            raise ValueError('evidence requires actual cost')
        return self


class CostService:
    def __init__(self, store: CostStore):
        self.store = store

    def prepare(self, facts: CostAttempt) -> CostAttempt:
        facts = CostAttempt.model_validate(facts)
        validate_safe_metadata(facts.usage, facts.specifications)
        if facts.submission_status != 'pending' or facts.execution_status != 'pending' or facts.external_id is not None:
            raise ValueError('prepare requires unsent pending intent')
        return self.store.create_attempt(facts)

    def submitted(self, attempt_id: str, external_id: str):
        def apply():
            old = self.store.get_attempt(attempt_id)
            if old.submission_status == 'failed':
                raise ValueError('unsent failed attempt cannot be submitted')
            execution = old.execution_status if old.execution_status in ('succeeded', 'failed', 'cancelled') else 'running'
            self.store.update_attempt(attempt_id, dict(external_id=external_id, submission_status='submitted', execution_status=execution))
        self.store.apply_observation(attempt_id, 'lifecycle:submitted', {'external_id': external_id}, apply)
        return self.store.get_attempt(attempt_id)

    def mark_submission_unknown(self, attempt_id: str):
        def apply():
            old = self.store.get_attempt(attempt_id)
            if old.submission_status in ('pending', 'unknown'):
                self.store.update_attempt(attempt_id, dict(submission_status='unknown', execution_status='unknown'))
        self.store.apply_observation(attempt_id, 'lifecycle:unknown', {}, apply)
        return self.store.get_attempt(attempt_id)

    def failed_before_send(self, attempt_id: str):
        attempt = self.store.get_attempt(attempt_id)
        if attempt.submission_status != 'pending':
            raise ValueError('only unsent pending attempts can fail before send')
        return self.store.update_attempt(attempt_id, dict(submission_status='failed', execution_status='failed'))

    def find_by_external(self, provider: str, account_id: str, external_id: str):
        return self.store.find_by_external(provider, account_id, external_id)

    def observe(self, attempt_id: str, event_id: str, facts: Observation) -> CostValue:
        facts = Observation.model_validate(facts)
        def apply():
            attempt = self.store.get_attempt(attempt_id)
            changes = {'usage': {**attempt.usage, **facts.usage}}
            if facts.execution_status is not None and not (
                attempt.execution_status in ('succeeded', 'failed', 'cancelled')
                and facts.execution_status in ('pending', 'running', 'unknown')
            ):
                changes['execution_status'] = facts.execution_status
            if facts.usage_source is not None:
                changes['usage_source'] = facts.usage_source
            attempt = self.store.update_attempt(attempt_id, changes)
            value, snapshot, evidence = self._price(attempt, facts)
            self.store.record_cost(attempt_id, event_id, value, snapshot, evidence)
        return self.store.apply_observation(attempt_id, event_id, facts, apply)

    def _price(self, attempt, facts):
        if facts.actual_cost is not None:
            evidence = facts.evidence
            rate = evidence.cny_rate or (Decimal(1) if evidence.original_currency == 'CNY' else None)
            audit = evidence.model_dump(mode='json', exclude_none=True)
            if rate is None:
                return CostValue(status='unpriced', reason='Missing CNY exchange rate'), None, audit
            if evidence.original_currency == 'CNY' and rate != 1:
                raise ValueError('CNY exchange rate must equal one')
            amount = charge_micros(quantity='1', unit_price=evidence.original_amount, basis='1', step='1', minimum='0', cny_rate=rate)
            if amount != facts.actual_cost.amount_micros:
                raise ValueError('actual cost disagrees with original amount and exchange rate')
            return facts.actual_cost, None, audit
        if attempt.submission_status in ('pending', 'failed'):
            return CostValue(status='unpriced', reason='Not submitted; excluded from cost'), None, {}
        for sub in self.store.list_subscriptions():
            if (sub.provider == attempt.provider and sub.account_id == attempt.account_id
                    and sub.starts_at <= attempt.occurred_at and (sub.ends_at is None or attempt.occurred_at < sub.ends_at)):
                return CostValue(status='subscription_covered', reason=f'Subscription {sub.id}'), None, {}
        snapshots = [r['rule_snapshot'] for r in self.store.list_revisions(attempt.attempt_id) if r.get('rule_snapshot')]
        rules = [PriceRule.model_validate(snapshots[0])] if snapshots else self.store.list_price_rules()
        result = quote(attempt, rules)
        if not attempt.usage_source or (attempt.execution_status in ('failed', 'cancelled', 'unknown') and attempt.usage_source == 'request'):
            return CostValue(status='unpriced', reason='No measured billable usage'), result.rule_snapshot, {}
        return result.cost, result.rule_snapshot, {}


@lru_cache(maxsize=None)
def _service_for_path(path):
    return CostService(CostStore(path))


def get_cost_service() -> CostService:
    from novelvideo.config import STATE_DIR
    return _service_for_path(str(Path(STATE_DIR) / 'local' / 'costs.db'))
