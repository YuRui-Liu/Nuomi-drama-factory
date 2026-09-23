"""Small validated configuration records for the durable cost ledger."""
from decimal import Decimal
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import Identity, pricing_decimal


class Subscription(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    id: Identity
    provider: Identity
    account_id: Identity
    starts_at: AwareDatetime
    ends_at: AwareDatetime | None = None
    amount_micros: Annotated[int, Field(strict=True, ge=0, le=2**63-1)] | None = None
    currency: Identity = 'CNY'
    original_amount: Decimal | None = None
    cny_rate: Decimal | None = None
    source: str | None = None

    @field_validator('original_amount', 'cny_rate', mode='before')
    @classmethod
    def decimal_value(cls, value):
        return None if value is None else pricing_decimal(value)

    @model_validator(mode='after')
    def interval(self):
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise ValueError('ends_at must follow starts_at')
        if self.cny_rate is not None and self.cny_rate <= 0:
            raise ValueError('cny_rate must be positive')
        return self


class Coverage(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    project_id: Identity
    provider: Identity
    start_at: AwareDatetime
    complete: bool = False
    reason: str | None = None
    gaps: tuple[str, ...] = ()
