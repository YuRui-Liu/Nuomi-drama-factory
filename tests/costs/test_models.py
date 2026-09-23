from datetime import datetime, timezone
from decimal import Decimal
import importlib

import pytest
from pydantic import ValidationError


@pytest.fixture
def models():
    try:
        return importlib.import_module("novelvideo.costs.models")
    except ModuleNotFoundError:
        pytest.fail("Cost models must exist")


def attempt_data(**updates):
    return dict(attempt_id="a1", project_id="p1", provider="runninghub",
                account_id="account1", model="image-model", media_type="image",
                occurred_at=datetime.now(timezone.utc), **updates)


def test_unknown_and_subscription_are_null(models):
    for status in ("unpriced", "subscription_covered"):
        assert models.CostValue(status=status).amount_micros is None
        with pytest.raises(ValidationError):
            models.CostValue(status=status, amount_micros=0)


@pytest.mark.parametrize("status", ["confirmed", "estimated"])
def test_known_cost_requires_amount(models, status):
    with pytest.raises(ValidationError):
        models.CostValue(status=status)
    assert models.CostValue(status=status, amount_micros=123).amount_micros == 123


def test_confirmed_zero_requires_explicit_evidence(models):
    with pytest.raises(ValidationError):
        models.CostValue(status="confirmed", amount_micros=0, reason="  ")
    assert models.CostValue(status="confirmed", amount_micros=0,
                            reason="Provider explicitly reported zero").amount_micros == 0
    assert models.CostValue(status="estimated", amount_micros=0).amount_micros == 0


@pytest.mark.parametrize("amount", [-1, True, 1.5, 1.0, "1", Decimal("1")])
def test_amount_rejects_coercion(models, amount):
    with pytest.raises(ValidationError):
        models.CostValue(status="estimated", amount_micros=amount)


@pytest.mark.parametrize("field", ["attempt_id", "project_id", "provider", "account_id", "model"])
def test_attempt_requires_identity(models, field):
    data = attempt_data()
    for bad in (None, "", "  ", 42):
        data[field] = bad
        with pytest.raises(ValidationError):
            models.CostAttempt(**data)
    del data[field]
    with pytest.raises(ValidationError):
        models.CostAttempt(**data)


def test_time_requires_timezone_and_roundtrips(models):
    data = attempt_data()
    data["occurred_at"] = datetime(2026, 9, 23)
    with pytest.raises(ValidationError):
        models.CostAttempt(**data)
    data["occurred_at"] = "2026-09-23T08:00:00+08:00"
    attempt = models.CostAttempt(**data)
    assert attempt.occurred_at.utcoffset() is not None
    assert models.CostAttempt.model_validate_json(attempt.model_dump_json()) == attempt


@pytest.mark.parametrize("media", ["image", "audio", "video", "text"])
def test_media_types(models, media):
    data = attempt_data()
    data["media_type"] = media
    assert models.CostAttempt(**data).media_type == media


@pytest.mark.parametrize("field,value", [("media_type", "music"), ("execution_status", "oops"),
                                         ("submission_status", "oops")])
def test_invalid_statuses_and_media(models, field, value):
    data = attempt_data()
    data[field] = value
    with pytest.raises(ValidationError):
        models.CostAttempt(**data)


def test_invalid_cost_status(models):
    with pytest.raises(ValidationError):
        models.CostValue(status="free")


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "-0.1", "garbage", 0.1, True, b"1.2"])
def test_usage_rejects_unsafe_values(models, value):
    with pytest.raises(ValidationError):
        models.CostAttempt(**attempt_data(usage={"seconds": value}))


def test_usage_preserves_decimal_precision_and_source(models):
    attempt = models.CostAttempt(**attempt_data(
        usage={"seconds": "0.123456789123456789", "tokens": "0"},
        usage_source="provider_response", external_id="provider-job"))
    assert attempt.usage["seconds"] == "0.123456789123456789"
    assert attempt.usage_source == "provider_response"
