"""Decimal pricing with fail-closed matching and immutable audit snapshots.

quote(attempt, rules) returns PriceQuote(cost, rule_snapshot). Usage keys are the
PriceItem.unit names. Every item requires measured usage, including call/item.
validate_rules must run before saving a complete rule collection.
"""
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP, localcontext
from itertools import combinations
from typing import Iterable

from .models import CostAttempt, CostValue, PriceQuote, PriceRule, pricing_decimal

SCOPES = ('provider', 'account_id', 'model', 'workflow')
MAX_MICROS = 2**63 - 1


def _charge(*, quantity, unit_price, basis, step, minimum, cny_rate):
    q, p, m = (pricing_decimal(v) for v in (quantity, unit_price, minimum))
    b, s, r = (pricing_decimal(v, positive=True) for v in (basis, step, cny_rate))
    return (max(q, m) / s).to_integral_value(rounding=ROUND_CEILING) * s / b * p * r


def _micros(amount):
    scaled = (amount * Decimal(1000000)).to_integral_value(rounding=ROUND_HALF_UP)
    if scaled > MAX_MICROS:
        raise ValueError('cost exceeds signed 64-bit micro amount')
    return int(scaled)


def charge_micros(*, quantity, unit_price, basis, step, minimum, cny_rate) -> int:
    """Ceil to usage step after minimum, convert to CNY, round once HALF_UP."""
    with localcontext() as context:
        context.prec = 400
        return _micros(_charge(quantity=quantity, unit_price=unit_price, basis=basis,
                               step=step, minimum=minimum, cny_rate=cny_rate))


def _specificity(rule):
    return sum(getattr(rule, key) is not None for key in SCOPES) + len(rule.specifications)


def _matches(attempt, rule):
    return (rule.media_type == attempt.media_type
            and rule.starts_at <= attempt.occurred_at
            and (rule.ends_at is None or attempt.occurred_at < rule.ends_at)
            and all(getattr(rule, key) is None or getattr(rule, key) == getattr(attempt, key) for key in SCOPES)
            and all(dict(attempt.specifications).get(k) == v for k, v in rule.specifications))


def validate_rules(rules: Iterable[PriceRule]) -> None:
    """Reject duplicate identities and equally specific intersecting scope/time rules."""
    rules = tuple(rules)
    if len({(r.id, r.version) for r in rules}) != len(rules):
        raise ValueError('duplicate price id/version')
    for a, b in combinations(rules, 2):
        if a.media_type != b.media_type or _specificity(a) != _specificity(b):
            continue
        if (a.ends_at is not None and a.ends_at <= b.starts_at) or (b.ends_at is not None and b.ends_at <= a.starts_at):
            continue
        if any(getattr(a, k) is not None and getattr(b, k) is not None and getattr(a, k) != getattr(b, k) for k in SCOPES):
            continue
        sa, sb = dict(a.specifications), dict(b.specifications)
        if any(sa[k] != sb[k] for k in sa.keys() & sb.keys()):
            continue
        raise ValueError('equally specific overlapping price rules')


def quote(attempt: CostAttempt, rules: Iterable[PriceRule]) -> PriceQuote:
    matches = [r for r in rules if _matches(attempt, r)]
    if not matches:
        return PriceQuote(cost=CostValue(status='unpriced', reason='No matching price rule'))
    specificity = max(map(_specificity, matches))
    winners = [r for r in matches if _specificity(r) == specificity]
    if len(winners) != 1:
        return PriceQuote(cost=CostValue(status='unpriced', reason='Ambiguous price rules'))
    rule = winners[0]
    rate = rule.cny_rate if rule.cny_rate is not None else (Decimal(1) if rule.currency == 'CNY' else None)
    if rate is None:
        return PriceQuote(cost=CostValue(status='unpriced', reason='Missing CNY exchange rate'), rule_snapshot=rule)
    if any(item.unit not in attempt.usage for item in rule.items):
        return PriceQuote(cost=CostValue(status='unpriced', reason='Missing required usage'), rule_snapshot=rule)
    with localcontext() as context:
        context.prec = 400
        amount = sum((_charge(quantity=attempt.usage[item.unit], unit_price=item.unit_price,
                             basis=item.basis, step=item.step, minimum=item.minimum,
                             cny_rate=rate) for item in rule.items), Decimal(0))
        return PriceQuote(cost=CostValue(status='estimated', amount_micros=_micros(amount)), rule_snapshot=rule)
