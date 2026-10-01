"""Project-scoped presentation queries over an atomic ledger snapshot."""
import base64
from datetime import datetime, time, timedelta
import json
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, TypeAdapter

BEIJING = ZoneInfo('Asia/Shanghai')


def _datetime(value):
    return TypeAdapter(AwareDatetime).validate_python(value).astimezone(BEIJING)


def _cents(micros):
    return None if micros is None else (micros + 5000) // 10000


def _bucket():
    return dict(total_cents=0, confirmed_cents=0, estimated_cents=0,
                unpriced_count=0, subscription_count=0, pending_count=0, attempt_count=0,
                priced_count=0, cny_attempt_count=0, native_credit_count=0, native_credit_total='0')


def _rh_billing(attempt):
    if attempt.provider != 'runninghub' or attempt.submission_status != 'submitted':
        return None
    amount = attempt.usage.get('credit') if attempt.usage_source == 'provider' else None
    return dict(unit='RH_CREDIT', amount=amount, status='confirmed' if amount is not None else 'unknown')


def _display_status(attempt, cost):
    billing = _rh_billing(attempt)
    return ('confirmed' if billing['amount'] is not None else 'unpriced') if billing else cost.status


def _add(bucket, attempt, cost):
    bucket['attempt_count'] += 1
    if attempt.provider != 'runninghub':
        bucket['cny_attempt_count'] += 1
    if attempt.submission_status == 'pending':
        bucket['pending_count'] += 1
    elif attempt.submission_status == 'unknown':
        bucket['unpriced_count'] += 1
    elif attempt.submission_status == 'submitted':
        billing = _rh_billing(attempt)
        if billing is not None:
            if billing['amount'] is None:
                bucket['unpriced_count'] += 1
            else:
                bucket['native_credit_count'] += 1
                bucket['native_credit_total'] = str(Decimal(bucket['native_credit_total']) + Decimal(billing['amount']))
        elif cost.status in ('confirmed', 'estimated'):
            bucket['priced_count'] += 1
            amount = _cents(cost.amount_micros)
            bucket[cost.status + '_cents'] += amount
            bucket['total_cents'] += amount
        elif cost.status == 'subscription_covered':
            bucket['subscription_count'] += 1
        else:
            bucket['unpriced_count'] += 1


class CostQueries:
    def __init__(self, store):
        self.store = store

    def snapshot(self, project_id, now, created_at=None):
        now = _datetime(now)
        data = self.store.snapshot(project_id)
        all_attempts = [a for a in data['attempts'] if a.project_id == project_id]
        coverage = [c for c in data['coverage'] if c.project_id == project_id]
        start = _datetime(created_at) if created_at is not None else min(
            [now, *[a.occurred_at.astimezone(BEIJING) for a in all_attempts],
             *[c.start_at.astimezone(BEIJING) for c in coverage]])
        if start > now:
            raise ValueError('created_at must not follow now')
        attempts = [a for a in all_attempts if start <= a.occurred_at <= now]
        reasons = []
        if created_at is None:
            reasons.append('project_creation_unknown')
        providers = {a.provider for a in attempts}
        covered = {c.provider for c in coverage}
        if not coverage or providers - covered:
            reasons.append('coverage_missing')
        gaps = sorted({g for c in coverage for g in c.gaps})
        for c in coverage:
            if not c.complete:
                reasons.append(c.reason or 'coverage_incomplete')
            if c.start_at > start:
                reasons.append('history_before_monitoring')
        if gaps:
            reasons.append('coverage_gaps')
        # Gap records are descriptive strings, not reliable date intervals. Treat
        # their scope conservatively until storage supplies structured boundaries.
        coverage_proven = bool(coverage) and not (providers - covered) and all(c.complete and not c.gaps for c in coverage)
        monitoring_start = max((c.start_at for c in coverage), default=None)
        summary = _bucket()
        days, channels, media = {}, {}, {}
        priced = False
        priced_days = set()
        for attempt in attempts:
            cost = data['costs'][attempt.attempt_id]
            day = attempt.occurred_at.astimezone(BEIJING).date().isoformat()
            channel = channels.setdefault(attempt.provider, dict(**_bucket(), media={}))
            for bucket in (summary, days.setdefault(day, _bucket()), channel,
                           channel['media'].setdefault(attempt.media_type, _bucket()),
                           media.setdefault(attempt.media_type, _bucket())):
                _add(bucket, attempt, cost)
            if attempt.provider != 'runninghub' and attempt.submission_status == 'submitted' and cost.status in ('confirmed', 'estimated'):
                priced = True
                priced_days.add(day)
        if summary['unpriced_count']:
            reasons.append('unpriced_attempts')
        if summary['pending_count']:
            reasons.append('pending_submissions')
        summary['complete'] = not reasons
        summary['display_state'] = ('priced' if priced else 'unpriced_only' if summary['unpriced_count'] or summary['pending_count']
                                    else 'subscription_only' if summary['subscription_count'] else 'empty')
        daily, cumulative = [], []
        running = dict(confirmed_cents=0, estimated_cents=0, total_cents=0, unpriced_count=0)
        cumulative_complete = created_at is not None
        day = start.date()
        while day <= now.date():
            key = day.isoformat()
            bucket = days.get(key, _bucket())
            interval_start = max(start, datetime.combine(day, time.min, BEIJING))
            complete = coverage_proven and monitoring_start <= interval_start and not (bucket['unpriced_count'] or bucket['pending_count'])
            point = dict(date=key, **{k: bucket[k] for k in running}, complete=bool(complete))
            if not complete and key not in priced_days:
                for name in ('confirmed_cents', 'estimated_cents', 'total_cents'):
                    point[name] = None
            daily.append(point)
            for name in running:
                running[name] += bucket[name]
            cumulative_complete = cumulative_complete and bool(complete)
            cumulative.append(dict(date=key, **running, complete=cumulative_complete))
            day += timedelta(days=1)
        subscriptions = []
        measured_credits = {}
        measured_external = set()
        for attempt in attempts:
            if attempt.submission_status == 'submitted' and attempt.usage_source == 'provider' and 'credit' in attempt.usage:
                key = (attempt.provider, attempt.account_id)
                measured_credits[key] = measured_credits.get(key, Decimal(0)) + Decimal(attempt.usage['credit'])
                measured_external.add((attempt.provider, attempt.external_id))
        legacy_receipts = [r for r in data.get('historical_receipts', [])
                           if (r['provider'], r['external_id']) not in measured_external]
        associated = {(a.provider, a.account_id) for a in attempts if a.submission_status in ('submitted', 'unknown')}
        for sub in data['subscriptions']:
            if (sub.provider, sub.account_id) not in associated or sub.starts_at > now or (sub.ends_at is not None and sub.ends_at <= start):
                continue
            value = sub.model_dump(mode='json')
            value['amount_cents'] = _cents(sub.amount_micros)
            value['project_call_count'] = sum(a.submission_status == 'submitted' and (a.provider, a.account_id) == (sub.provider, sub.account_id)
                                             and sub.starts_at <= a.occurred_at and (sub.ends_at is None or a.occurred_at < sub.ends_at) for a in attempts)
            subscriptions.append(value)
        return dict(project_id=project_id, currency='CNY', timezone='Asia/Shanghai', snapshot_at=now.isoformat(),
                    range={'from': start.isoformat(), 'to': now.isoformat()}, summary=summary,
                    trend=dict(daily=daily, cumulative=cumulative),
                    breakdown=dict(channels=[dict(provider=p, **{k: v for k, v in b.items() if k != 'media'},
                                                  media=[dict(media_type=m, **v) for m, v in sorted(b['media'].items())])
                                             for p, b in sorted(channels.items())],
                                   media=[dict(media_type=m, **v) for m, v in sorted(media.items())]),
                    subscriptions=sorted(subscriptions, key=lambda s: s['id']),
                    measured_credits=[dict(provider=p, account_id=a, credit=str(value))
                                      for (p, a), value in sorted(measured_credits.items())],
                    historical_credits=dict(provider='runninghub', credit=str(sum((Decimal(r['credit']) for r in legacy_receipts), Decimal(0))),
                                            task_count=len(legacy_receipts), date_known=False),
                    coverage=dict(start_at=monitoring_start.astimezone(BEIJING).isoformat() if monitoring_start else None,
                                  complete=summary['complete'], reasons=sorted(set(reasons)), gaps=gaps))

    @staticmethod
    def _entry(attempt, cost):
        return dict(**attempt.model_dump(mode='json'), cost_status=_display_status(attempt, cost),
                    billing=_rh_billing(attempt),
                    amount_cents=_cents(cost.amount_micros) if attempt.submission_status == 'submitted' and attempt.provider != 'runninghub' else None,
                    value=cost.model_dump(mode='json'))

    def entries(self, project_id, channel=None, media=None, status=None, cursor=None, limit=50):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError('limit must be an integer from 1 to 500')
        scope = [project_id, channel, media, status]
        before = None
        if cursor is not None:
            try:
                if not isinstance(cursor, str) or len(cursor) > 8192:
                    raise ValueError()
                decoded = json.loads(base64.b64decode(cursor, altchars=b'-_', validate=True))
                if decoded['scope'] != scope or not isinstance(decoded['id'], str) or not decoded['id']:
                    raise ValueError()
                before = (_datetime(decoded['at']), decoded['id'])
            except (ValueError, TypeError, KeyError, UnicodeError) as exc:
                raise ValueError('invalid cursor for project and filters') from exc
        data = self.store.snapshot(project_id)
        rows = [a for a in data['attempts'] if a.project_id == project_id
                and (channel is None or a.provider == channel) and (media is None or a.media_type == media)
                and (status is None or _display_status(a, data['costs'][a.attempt_id]) == status)
                and (before is None or (a.occurred_at, a.attempt_id) < before)]
        rows.sort(key=lambda a: (a.occurred_at, a.attempt_id), reverse=True)
        selected = rows[:limit]
        next_cursor = None
        if len(rows) > limit:
            last = selected[-1]
            next_cursor = base64.urlsafe_b64encode(json.dumps(dict(scope=scope, at=last.occurred_at.isoformat(), id=last.attempt_id)).encode()).decode()
        return dict(entries=[self._entry(a, data['costs'][a.attempt_id]) for a in selected], next_cursor=next_cursor)

    def entry_detail(self, project_id, attempt_id):
        data = self.store.get_entry_detail(project_id, attempt_id)
        attempt, cost = data['attempt'], data['cost']
        current = data['current']
        current.update(value=cost.model_dump(mode='json'),
                       billing=_rh_billing(attempt),
                       amount_cents=_cents(cost.amount_micros) if attempt.submission_status == 'submitted' and attempt.provider != 'runninghub' else None)
        return dict(attempt=attempt.model_dump(mode='json'), current_cost=current,
                    value=cost.model_dump(mode='json'), revisions=data['revisions'])
