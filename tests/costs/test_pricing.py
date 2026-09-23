from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest

from novelvideo.costs.models import CostAttempt, PriceRule, PriceItem
from novelvideo.costs.pricing import charge_micros, quote, validate_rules

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)


def attempt(**kw):
    return CostAttempt(**(dict(attempt_id='a', project_id='p', provider='vendor',
        account_id='acct', model='m', media_type='text', occurred_at=NOW,
        usage={'second': '10.1'}) | kw))


def rule(**kw):
    return PriceRule(**(dict(id='r', version='v1', media_type='text', starts_at=NOW,
        items=[dict(unit='second', unit_price='1.2')]) | kw))


def test_step_rounding():
    assert charge_micros(quantity='10.1', unit_price='1.2', basis='1', step='1',
                         minimum='0', cny_rate='1') == 13200000


@pytest.mark.parametrize('field', ['quantity', 'unit_price', 'basis', 'step', 'minimum', 'cny_rate'])
@pytest.mark.parametrize('bad', ['-1', 'NaN', 'Infinity'])
def test_invalid_charge(field, bad):
    args = dict(quantity='1', unit_price='1', basis='1', step='1', minimum='0', cny_rate='1')
    args[field] = bad
    with pytest.raises(ValueError):
        charge_micros(**args)


@pytest.mark.parametrize('field', ['basis', 'step', 'cny_rate'])
def test_zero_denominator(field):
    args = dict(quantity='1', unit_price='1', basis='1', step='1', minimum='0', cny_rate='1')
    args[field] = '0'
    with pytest.raises(ValueError):
        charge_micros(**args)


def test_quote_unknown_and_zero():
    assert quote(attempt(usage={}), [rule()]).cost.status == 'unpriced'
    assert quote(attempt(), [rule(currency='USD')]).cost.status == 'unpriced'
    result = quote(attempt(), [rule(items=[dict(unit='second', unit_price='0')])])
    assert result.cost.amount_micros == 0
    assert result.rule_snapshot.id == 'r'


def test_tokens_round_together():
    r = rule(items=[dict(unit=u, unit_price='0.0000004') for u in ('input_tokens', 'output_tokens')])
    assert quote(attempt(usage={'input_tokens':'1', 'output_tokens':'1'}), [r]).cost.amount_micros == 1


def test_selection_scope_specificity_and_time():
    generic = rule()
    exact = rule(id='exact', provider='vendor', model='m', workflow='wf',
                 specifications=(('size', 'large'),))
    a = attempt(workflow='wf', specifications=(('size', 'large'),))
    assert quote(a, [generic, exact]).rule_snapshot.id == 'exact'
    assert quote(attempt(), [exact]).cost.status == 'unpriced'
    past = rule(ends_at=NOW + timedelta(seconds=1))
    future = rule(version='v2', starts_at=NOW + timedelta(seconds=1))
    validate_rules([past, future])
    assert quote(attempt(occurred_at=NOW + timedelta(seconds=1)), [past, future]).rule_snapshot.version == 'v2'


def test_overlap_and_fail_closed():
    rules = [rule(id='a', provider='vendor'), rule(id='b', model='m')]
    with pytest.raises(ValueError):
        validate_rules(rules)
    assert quote(attempt(), rules).cost.status == 'unpriced'
    validate_rules([rule(provider='one'), rule(id='two', provider='two')])


def test_invalid_rule_numbers_and_dates():
    for value in ('-1', 'NaN', 'Infinity'):
        with pytest.raises(ValueError):
            PriceItem(unit='call', unit_price=value)
    with pytest.raises(ValueError):
        rule(starts_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError):
        rule(ends_at=NOW)


def test_minimum_basis_conversion_and_snapshot_roundtrip():
    assert charge_micros(quantity='0', unit_price='2', basis='100', step='3',
                         minimum='4', cny_rate='7') == 840000
    result = quote(attempt(), [rule(currency='USD', cny_rate='7')])
    assert result.cost.amount_micros == 92400000
    assert type(result).model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize('unit', ['item', 'second', 'call', 'character', 'input_tokens', 'output_tokens', 'credit'])
def test_all_metering_units(unit):
    result = quote(attempt(usage={unit:'2'}), [rule(items=[dict(unit=unit, unit_price='3')])])
    assert result.cost.amount_micros == 6000000


def test_scope_and_specs_are_immutable():
    specifications = [('size', 'large')]
    a = attempt(specifications=specifications)
    specifications[0] = ('size', 'small')
    assert a.specifications == (('size', 'large'),)
    with pytest.raises(ValueError):
        a.workflow = 'changed'


def test_bounded_numeric_and_overflow():
    for value in ('1e1000000', '9' * 51, 1.2):
        with pytest.raises(ValueError):
            PriceItem(unit='call', unit_price=value)
    with pytest.raises(ValueError, match='64-bit'):
        charge_micros(quantity='10000000000000', unit_price='1', basis='1', step='1', minimum='0', cny_rate='1')


def test_rule_rate_and_denominators_reject_zero():
    with pytest.raises(ValueError):
        rule(cny_rate='0')
    for field in ('basis', 'step'):
        with pytest.raises(ValueError):
            PriceItem(**dict(unit='call', unit_price='1', **{field:'0'}))
