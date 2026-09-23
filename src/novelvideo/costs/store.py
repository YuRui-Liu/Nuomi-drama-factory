"""SQLite cost ledger. Every write is serialized; snapshot() reads one transaction."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from pydantic import AwareDatetime, TypeAdapter

from novelvideo.sqlite_pragmas import configure_sqlite_connection
from .models import CostAttempt, CostValue, PriceRule
from .pricing import MAX_MICROS, validate_rules
from .storage_models import Coverage, Subscription


class StoreConflictError(ValueError):
    """An immutable identity or replay payload conflicts with existing data."""


def _json(value):
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


class CostStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection(write=True) as db:
            for statement in (
                '''CREATE TABLE IF NOT EXISTS cost_attempts (
                attempt_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                provider TEXT NOT NULL, account_id TEXT NOT NULL, media_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL, external_id TEXT, body_json TEXT NOT NULL,
                UNIQUE(provider, account_id, external_id))''',
                '''CREATE INDEX IF NOT EXISTS cost_project ON cost_attempts(project_id, occurred_at)''',
                '''CREATE TABLE IF NOT EXISTS current_costs (
                attempt_id TEXT PRIMARY KEY REFERENCES cost_attempts(attempt_id),
                amount_micros INTEGER CHECK(amount_micros >= 0), body_json TEXT NOT NULL)''',
                '''CREATE TABLE IF NOT EXISTS cost_revisions (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                attempt_id TEXT NOT NULL REFERENCES cost_attempts(attempt_id),
                event_id TEXT NOT NULL, amount_micros INTEGER CHECK(amount_micros >= 0),
                applied INTEGER NOT NULL, body_json TEXT NOT NULL, recorded_at TEXT,
                UNIQUE(attempt_id,event_id))''',
                '''CREATE TABLE IF NOT EXISTS price_versions (
                id TEXT NOT NULL, version TEXT NOT NULL, body_json TEXT NOT NULL,
                PRIMARY KEY(id,version))''',
                '''CREATE TABLE IF NOT EXISTS subscriptions (
                id TEXT PRIMARY KEY, provider TEXT NOT NULL, account_id TEXT NOT NULL,
                amount_micros INTEGER CHECK(amount_micros >= 0), body_json TEXT NOT NULL)''',
                '''CREATE TABLE IF NOT EXISTS coverage (
                project_id TEXT NOT NULL, provider TEXT NOT NULL, body_json TEXT NOT NULL,
                PRIMARY KEY(project_id,provider))''',
            ):
                db.execute(statement)
            columns = {row['name'] for row in db.execute('PRAGMA table_info(cost_revisions)')}
            if 'recorded_at' not in columns:
                # Historical write times cannot be reconstructed; keep them unknown.
                db.execute('ALTER TABLE cost_revisions ADD COLUMN recorded_at TEXT')

    @contextmanager
    def _connection(self, *, write=False):
        db = sqlite3.connect(str(self.path), timeout=10)
        try:
            db.row_factory = sqlite3.Row
            configure_sqlite_connection(db)
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            db.commit()
        except sqlite3.IntegrityError as exc:
            db.rollback()
            raise StoreConflictError(str(exc)) from exc
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create_attempt(self, facts):
        attempt = CostAttempt.model_validate(facts)
        body = _json(attempt)
        with self._connection(write=True) as db:
            old = db.execute('SELECT body_json FROM cost_attempts WHERE attempt_id=?', (attempt.attempt_id,)).fetchone()
            if old:
                if old[0] != body:
                    raise StoreConflictError('attempt identity already registered with different facts')
                return attempt
            db.execute('INSERT INTO cost_attempts VALUES (?,?,?,?,?,?,?,?)',
                       (attempt.attempt_id, attempt.project_id, attempt.provider, attempt.account_id,
                        attempt.media_type, attempt.occurred_at.isoformat(), attempt.external_id, body))
            db.execute('INSERT INTO current_costs VALUES (?,NULL,?)',
                       (attempt.attempt_id, _json(CostValue(status='unpriced'))))
        return attempt

    def update_attempt(self, attempt_id, changes):
        with self._connection(write=True) as db:
            old = self._attempt(db, attempt_id)
            data = old.model_dump()
            data.update(changes)
            updated = CostAttempt.model_validate(data)
            for name in ('attempt_id', 'project_id', 'provider', 'account_id', 'occurred_at'):
                if getattr(updated, name) != getattr(old, name):
                    raise ValueError(f'{name} is immutable')
            if old.external_id is not None and updated.external_id != old.external_id:
                raise ValueError('external_id cannot be replaced')
            db.execute('UPDATE cost_attempts SET external_id=?,media_type=?,body_json=? WHERE attempt_id=?',
                       (updated.external_id, updated.media_type, _json(updated), attempt_id))
            return updated

    @staticmethod
    def _attempt(db, attempt_id):
        row = db.execute('SELECT body_json FROM cost_attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
        if not row:
            raise KeyError(attempt_id)
        return CostAttempt.model_validate_json(row[0])

    def get_attempt(self, attempt_id):
        with self._connection() as db:
            return self._attempt(db, attempt_id)

    def find_by_external(self, provider, account_id, external_id):
        with self._connection() as db:
            row = db.execute('SELECT body_json FROM cost_attempts WHERE provider=? AND account_id=? AND external_id=?',
                             (provider, account_id, external_id)).fetchone()
            return CostAttempt.model_validate_json(row[0]) if row else None

    def list_attempts(self, project_id=None):
        with self._connection() as db:
            sql = 'SELECT body_json FROM cost_attempts'
            rows = db.execute(sql + (' WHERE project_id=?' if project_id else '') + ' ORDER BY occurred_at,attempt_id',
                              (project_id,) if project_id else ())
            return [CostAttempt.model_validate_json(row[0]) for row in rows]

    def record_cost(self, attempt_id, event_id, value, rule_snapshot=None, evidence=None):
        value = CostValue.model_validate(value)
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError('event_id is required')
        if value.amount_micros is not None and value.amount_micros > MAX_MICROS:
            raise ValueError('cost exceeds signed 64-bit micros')
        if rule_snapshot is not None:
            rule_snapshot = PriceRule.model_validate(rule_snapshot).model_dump(mode='json')
        evidence = {} if evidence is None else evidence
        allowed = {'type', 'source', 'original_amount', 'original_currency', 'cny_rate', 'reason'}
        if not isinstance(evidence, dict) or set(evidence) - allowed:
            raise ValueError('unsupported evidence fields')
        if any(not isinstance(v, (str, int, type(None))) or isinstance(v, bool) for v in evidence.values()):
            raise ValueError('evidence must contain scalar audit facts')
        body = _json(dict(value=value.model_dump(mode='json'), rule_snapshot=rule_snapshot, evidence=evidence))
        with self._connection(write=True) as db:
            self._attempt(db, attempt_id)
            old = db.execute('SELECT body_json FROM cost_revisions WHERE attempt_id=? AND event_id=?', (attempt_id,event_id)).fetchone()
            if old:
                if old[0] != body:
                    raise StoreConflictError('event replay has a different payload')
                return False
            current = CostValue.model_validate_json(db.execute('SELECT body_json FROM current_costs WHERE attempt_id=?', (attempt_id,)).fetchone()[0])
            applied = not (current.status == 'confirmed' and value.status != 'confirmed')
            db.execute('INSERT INTO cost_revisions(attempt_id,event_id,amount_micros,applied,body_json,recorded_at) VALUES (?,?,?,?,?,?)',
                       (attempt_id,event_id,value.amount_micros,int(applied),body,datetime.now(timezone.utc).isoformat()))
            if applied:
                db.execute('UPDATE current_costs SET amount_micros=?,body_json=? WHERE attempt_id=?',
                           (value.amount_micros,_json(value),attempt_id))
            return applied

    def get_cost(self, attempt_id):
        with self._connection() as db:
            row = db.execute('SELECT body_json FROM current_costs WHERE attempt_id=?', (attempt_id,)).fetchone()
            if row is None:
                raise KeyError(attempt_id)
            return CostValue.model_validate_json(row[0])

    def list_revisions(self, attempt_id):
        with self._connection() as db:
            return [dict(json.loads(row['body_json']), event_id=row['event_id'], applied=bool(row['applied']), sequence=row['sequence'], recorded_at=row['recorded_at'])
                    for row in db.execute('SELECT * FROM cost_revisions WHERE attempt_id=? ORDER BY sequence', (attempt_id,))]

    @staticmethod
    def _cost_details(db, ids):
        details = {}
        for row in db.execute('SELECT * FROM cost_revisions WHERE applied=1 ORDER BY sequence'):
            if row['attempt_id'] in ids:
                details[row['attempt_id']] = dict(json.loads(row['body_json']), event_id=row['event_id'], sequence=row['sequence'], recorded_at=row['recorded_at'])
        return details

    def get_cost_record(self, attempt_id):
        with self._connection() as db:
            self._attempt(db, attempt_id)
            return self._cost_details(db, {attempt_id}).get(attempt_id, dict(
                value=CostValue(status='unpriced').model_dump(mode='json'), rule_snapshot=None,
                evidence={}, event_id=None, sequence=0, recorded_at=None))

    def add_price_rule(self, rule):
        rule = PriceRule.model_validate(rule)
        with self._connection(write=True) as db:
            rows = db.execute('SELECT body_json FROM price_versions').fetchall()
            rules = [PriceRule.model_validate_json(r[0]) for r in rows]
            for old in rules:
                if (old.id,old.version) == (rule.id,rule.version):
                    if old != rule:
                        raise StoreConflictError('price version is immutable')
                    return old
            validate_rules([*rules, rule])
            db.execute('INSERT INTO price_versions VALUES (?,?,?)', (rule.id,rule.version,_json(rule)))
        return rule

    def list_price_rules(self):
        with self._connection() as db:
            return [PriceRule.model_validate_json(r[0]) for r in db.execute('SELECT body_json FROM price_versions ORDER BY id,version')]

    def stop_price_rule(self, id, version, ends_at):
        ends_at = TypeAdapter(AwareDatetime).validate_python(ends_at)
        with self._connection(write=True) as db:
            rules = [PriceRule.model_validate_json(r[0]) for r in db.execute('SELECT body_json FROM price_versions')]
            old = next((r for r in rules if (r.id,r.version)==(id,version)), None)
            if old is None:
                raise KeyError((id,version))
            changed = PriceRule.model_validate({**old.model_dump(), 'ends_at': ends_at})
            if old.ends_at is not None and changed.ends_at > old.ends_at:
                raise ValueError('stopped price rule cannot be extended')
            validate_rules([changed if r == old else r for r in rules])
            db.execute('UPDATE price_versions SET body_json=? WHERE id=? AND version=?', (_json(changed),id,version))
            return changed

    def save_subscription(self, subscription):
        sub = Subscription.model_validate(subscription)
        with self._connection(write=True) as db:
            db.execute('INSERT INTO subscriptions VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET provider=excluded.provider, account_id=excluded.account_id, amount_micros=excluded.amount_micros, body_json=excluded.body_json',
                       (sub.id,sub.provider,sub.account_id,sub.amount_micros,_json(sub)))
        return sub

    def list_subscriptions(self):
        with self._connection() as db:
            return [Subscription.model_validate_json(r[0]) for r in db.execute('SELECT body_json FROM subscriptions ORDER BY id')]

    def set_coverage(self, coverage):
        value = Coverage.model_validate(coverage)
        with self._connection(write=True) as db:
            db.execute('INSERT INTO coverage VALUES (?,?,?) ON CONFLICT(project_id,provider) DO UPDATE SET body_json=excluded.body_json',
                       (value.project_id,value.provider,_json(value)))
        return value

    def get_coverage(self, project_id, provider=None):
        with self._connection() as db:
            rows = db.execute('SELECT body_json FROM coverage WHERE project_id=?' + (' AND provider=?' if provider else '') + ' ORDER BY provider',
                              (project_id,provider) if provider else (project_id,)).fetchall()
            values = [Coverage.model_validate_json(r[0]) for r in rows]
            return (values[0] if values else None) if provider else values

    def snapshot(self, project_id=None):
        """Atomic query input: attempts, costs by ID, rules, subscriptions, coverage."""
        with self._connection() as db:
            attempts = [CostAttempt.model_validate_json(r[0]) for r in db.execute(
                'SELECT body_json FROM cost_attempts' + (' WHERE project_id=?' if project_id else ''), (project_id,) if project_id else ())]
            ids = {a.attempt_id for a in attempts}
            costs = {r[0]: CostValue.model_validate_json(r[1]) for r in db.execute('SELECT attempt_id,body_json FROM current_costs') if r[0] in ids}
            return dict(attempts=attempts, costs=costs, cost_details=self._cost_details(db, ids),
                        rules=[PriceRule.model_validate_json(r[0]) for r in db.execute('SELECT body_json FROM price_versions')],
                        subscriptions=[Subscription.model_validate_json(r[0]) for r in db.execute('SELECT body_json FROM subscriptions')],
                        coverage=[Coverage.model_validate_json(r[0]) for r in db.execute('SELECT body_json FROM coverage' + (' WHERE project_id=?' if project_id else ''), (project_id,) if project_id else ())])
