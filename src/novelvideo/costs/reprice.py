"""Durable, project-bound preview/apply of previously unpriced attempts only."""
import json
from uuid import uuid4

from .pricing import quote
from .queries import _cents
from .store import StoreConflictError, _json


class RepriceService:
    def __init__(self, store):
        self.store = store

    def _quote(self, attempt):
        if attempt.provider == 'runninghub':
            return None  # Package credits are tracked independently of CNY.
        if attempt.submission_status != 'submitted' or not attempt.usage_source:
            return None
        if attempt.execution_status in ('failed', 'cancelled', 'unknown') and attempt.usage_source == 'request':
            return None
        for sub in self.store.list_subscriptions():
            if (sub.provider, sub.account_id) == (attempt.provider, attempt.account_id) and sub.starts_at <= attempt.occurred_at and (sub.ends_at is None or attempt.occurred_at < sub.ends_at):
                return None
        result = quote(attempt, self.store.list_price_rules())
        return result if result.cost.status == 'estimated' else None

    def _facts(self, attempt_id):
        return dict(attempt=self.store.get_attempt(attempt_id).model_dump(mode='json'),
                    cost=self.store.get_cost(attempt_id).model_dump(mode='json'),
                    revisions=self.store.list_revisions(attempt_id))

    def preview(self, project_id):
        with self.store.transaction() as db:
            records, bindings = [], []
            for attempt in self.store.list_attempts(project_id):
                if self.store.get_cost(attempt.attempt_id).status != 'unpriced':
                    continue
                result = self._quote(attempt)
                if result is None:
                    continue
                records.append(dict(attempt_id=attempt.attempt_id, old_cents=None,
                                    new_cents=_cents(result.cost.amount_micros),
                                    matched_rule_versions=[dict(id=result.rule_snapshot.id, version=result.rule_snapshot.version)]))
                bindings.append(dict(attempt_id=attempt.attempt_id, facts=self._facts(attempt.attempt_id), quote=result.model_dump(mode='json')))
            dto = dict(preview_id=str(uuid4()), project_id=project_id, records=records,
                       affected_count=len(records), total_cents=sum(r['new_cents'] for r in records))
            db.execute('INSERT INTO cost_reprice_previews(id,project_id,body_json) VALUES (?,?,?)',
                       (dto['preview_id'], project_id, _json(dict(dto=dto, bindings=bindings))))
            return dto

    def apply(self, project_id, preview_id):
        with self.store.transaction() as db:
            row = db.execute('SELECT * FROM cost_reprice_previews WHERE id=? AND project_id=?', (preview_id, project_id)).fetchone()
            if row is None:
                raise KeyError(preview_id)
            body = json.loads(row['body_json'])
            if row['applied']:
                return dict(body['dto'], applied=True)
            results = []
            for binding in body['bindings']:
                attempt_id = binding['attempt_id']
                result = self._quote(self.store.get_attempt(attempt_id))
                if self._facts(attempt_id) != binding['facts'] or result is None or result.model_dump(mode='json') != binding['quote']:
                    raise StoreConflictError('preview is stale; create another preview')
                results.append((attempt_id, result))
            for attempt_id, result in results:
                self.store.record_cost(attempt_id, 'reprice:' + preview_id, result.cost, result.rule_snapshot,
                                       {'type':'reprice', 'source':preview_id})
            db.execute('UPDATE cost_reprice_previews SET applied=1 WHERE id=?', (preview_id,))
            return dict(body['dto'], applied=True)
