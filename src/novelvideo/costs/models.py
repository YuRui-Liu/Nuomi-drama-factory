"""Validated attribution and monetary facts for provider attempts."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, StrictStr, StringConstraints,
    field_validator, model_validator,
)

Identity = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
CostStatus = Literal["confirmed", "estimated", "unpriced", "subscription_covered"]
MediaType = Literal["image", "audio", "video", "text"]
ExecutionStatus = Literal["pending", "running", "succeeded", "failed", "cancelled", "unknown"]
SubmissionStatus = Literal["pending", "submitted", "failed", "unknown"]


class CostValue(BaseModel):
    """Amounts are integer currency micros; unknown and subscription costs are null."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: CostStatus
    amount_micros: Annotated[int, Field(strict=True, ge=0)] | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def validate_amount(self) -> CostValue:
        if self.status in ("confirmed", "estimated"):
            if self.amount_micros is None:
                raise ValueError("known cost requires amount_micros")
        elif self.amount_micros is not None:
            raise ValueError("unpriced and subscription_covered costs require null amount")
        if self.status == "confirmed" and self.amount_micros == 0:
            if not self.reason or not self.reason.strip():
                raise ValueError("confirmed zero requires an explicit evidence reason")
        return self


class CostAttempt(BaseModel):
    """One attributable submission attempt, including attempts rejected before an ID exists.

    Adapters convert measured usage to decimal strings before constructing facts;
    floats are deliberately rejected to avoid silently preserving binary rounding.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt_id: Identity
    project_id: Identity
    provider: Identity
    account_id: Identity
    model: Identity
    media_type: MediaType
    occurred_at: AwareDatetime
    task_id: Identity | None = None
    resource_id: Identity | None = None
    external_id: Identity | None = None
    execution_status: ExecutionStatus = "pending"
    submission_status: SubmissionStatus = "pending"
    usage: dict[Identity, StrictStr] = Field(default_factory=dict)
    usage_source: Identity | None = None
    workflow: Identity | None = None
    specifications: tuple[tuple[Identity, StrictStr], ...] = ()

    @field_validator("usage")
    @classmethod
    def validate_usage(cls, usage: dict[str, str]) -> dict[str, str]:
        for value in usage.values():
            try:
                number = Decimal(value)
            except InvalidOperation as exc:
                raise ValueError("usage must contain decimal strings") from exc
            if not number.is_finite() or number < 0:
                raise ValueError("usage must be finite and nonnegative")
        return usage


MeteringUnit = Literal['item', 'second', 'call', 'character', 'input_tokens', 'output_tokens', 'credit']


def pricing_decimal(value: object, *, positive: bool = False) -> Decimal:
    """Bound precision/exponents and reject binary floating point inputs."""
    if isinstance(value, (float, bool)):
        raise ValueError('pricing requires decimal strings or Decimal values')
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError('invalid pricing decimal') from exc
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise ValueError('pricing must be finite and nonnegative; denominators must be positive')
    if len(number.as_tuple().digits) > 50 or abs(number.as_tuple().exponent) > 50:
        raise ValueError('pricing decimal exceeds supported precision or exponent')
    return number


class PriceItem(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    unit: MeteringUnit
    unit_price: Decimal
    basis: Decimal = Decimal('1')
    step: Decimal = Decimal('1')
    minimum: Decimal = Decimal('0')

    @field_validator('unit_price', 'minimum', mode='before')
    @classmethod
    def nonnegative(cls, value):
        return pricing_decimal(value)

    @field_validator('basis', 'step', mode='before')
    @classmethod
    def positive(cls, value):
        return pricing_decimal(value, positive=True)


class PriceRule(BaseModel):
    """Versioned price in currency units; active interval is [starts_at, ends_at)."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    id: Identity
    version: Identity
    provider: Identity | None = None
    account_id: Identity | None = None
    media_type: MediaType
    model: Identity | None = None
    workflow: Identity | None = None
    specifications: tuple[tuple[Identity, StrictStr], ...] = ()
    starts_at: AwareDatetime
    ends_at: AwareDatetime | None = None
    currency: Identity = 'CNY'
    cny_rate: Decimal | None = None
    items: Annotated[tuple[PriceItem, ...], Field(min_length=1)]

    @field_validator('cny_rate', mode='before')
    @classmethod
    def rate(cls, value):
        return None if value is None else pricing_decimal(value, positive=True)

    @model_validator(mode='after')
    def valid_rule(self):
        if self.currency == 'CNY' and self.cny_rate not in (None, Decimal('1')):
            raise ValueError('CNY prices require cny_rate to be omitted or equal to one')
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise ValueError('ends_at must follow starts_at')
        if len(dict(self.specifications)) != len(self.specifications):
            raise ValueError('duplicate specification keys')
        if len({item.unit for item in self.items}) != len(self.items):
            raise ValueError('duplicate metering units')
        return self


class PriceQuote(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    cost: CostValue
    rule_snapshot: PriceRule | None = None
