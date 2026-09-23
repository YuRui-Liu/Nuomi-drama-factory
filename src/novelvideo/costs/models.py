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
