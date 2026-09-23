"""Project-scoped presentation queries over an atomic ledger snapshot."""
import base64
from datetime import datetime, time, timedelta
import json
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, TypeAdapter

BEIJING = ZoneInfo('Asia/Shanghai')


def _datetime(value):
    return TypeAdapter(AwareDatetime).validate_python(value).astimezone(BEIJING)


def _cents(micros):
    return None if micros is None else (micros + 5000) // 10000


def _bucket():
    return dict(total_cents=0, confirmed_cents=0, estimated_cents=0,
                unpriced_count=0, subscription_count=0, pending_count=0, attempt_count=0)


def _add(bucket, attempt, cost):
    bucket['attempt_count'] += 1
    if attempt.submission_status == 'pending':
        bucket['pending_count'] += 1
    elif attempt.submission_status == 'unknown':
        bucket['unpriced_count'] += 1
    elif attempt.submission_status == 'submitted':
        if cost.status in ('confirmed', 'estimated'):
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
            if attempt.submission_status == 'submitted' and cost.status in ('confirmed', 'estimated'):
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
                    coverage=dict(start_at=monitoring_start.astimezone(BEIJING).isoformat() if monitoring_start else None,
                                  complete=summary['complete'], reasons=sorted(set(reasons)), gaps=gaps))

    @staticmethod
    def _entry(attempt, cost):
        return dict(**attempt.model_dump(mode='json'), cost_status=cost.status,
                    amount_cents=_cents(cost.amount_micros) if attempt.submission_status == 'submitted' else None,
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
                and (status is None or data['costs'][a.attempt_id].status == status)
                and (before is None or (a.occurred_at, a.attempt_id) < before)]
        rows.sort(key=lambda a: (a.occurred_at, a.attempt_id), reverse=True)
        selected = rows[:limit]
        next_cursor = None
        if len(rows) > limit:
            last = selected[-1]
            next_cursor = base64.urlsafe_b64encode(json.dumps(dict(scope=scope, at=last.occurred_at.isoformat(), id=last.attempt_id)).encode()).decode()
        return dict(entries=[self._entry(a, data['costs'][a.attempt_id]) for a in selected], next_cursor=next_cursor)

    def entry_detail(self, project_id, attempt_id):
        data = self.store.snapshot(project_id)
        attempt = next((a for a in data['attempts'] if a.attempt_id == attempt_id and a.project_id == project_id), None)
        if attempt is None:
            raise KeyError(attempt_id)
        cost = data['costs'][attempt_id]
        current = dict(data['cost_details'].get(attempt_id, {}))
        current.update(value=cost.model_dump(mode='json'),
                       amount_cents=_cents(cost.amount_micros) if attempt.submission_status == 'submitted' else None)
        return dict(attempt=attempt.model_dump(mode='json'), current_cost=current,
                    value=cost.model_dump(mode='json'), revisions=self.store.list_revisions(attempt_id))
