from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bookcraft.domain.enums import ServiceCategory


class PricingConfigurationError(Exception):
    pass


class PricingCalculationError(Exception):
    pass


class PaymentScheduleType(StrEnum):
    FULL_UPON_SIGNING = "100% upon signing"
    PERCENTAGE_MONTHLY = "Percentage + Monthly Installments"
    FIXED_MONTHLY = "Fixed Amount + Monthly Installments"
    ADVANCE_FINAL = "Advance + Final Payment (linked to service)"
    MILESTONE_BASED = "Milestone-Based Schedule"


class MoneyRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str = "USD"
    low: Decimal = Field(ge=0)
    high: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def high_not_less_than_low(self) -> MoneyRange:
        if self.high < self.low:
            msg = "money range high must be >= low"
            raise ValueError(msg)
        return self


class TimelineRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit: Literal["business_days"] = "business_days"
    low: int = Field(ge=0)
    high: int = Field(ge=0)

    @model_validator(mode="after")
    def high_not_less_than_low(self) -> TimelineRange:
        if self.high < self.low:
            msg = "timeline range high must be >= low"
            raise ValueError(msg)
        return self


class PaymentScheduleOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule_type: PaymentScheduleType
    initial_amount: Decimal | None = Field(default=None, ge=0)
    initial_percentage: Decimal | None = Field(default=None, ge=0, le=100)
    remaining_amount: Decimal | None = Field(default=None, ge=0)
    remaining_percentage: Decimal | None = Field(default=None, ge=0, le=100)
    number_of_months: int | None = Field(default=None, ge=1)
    installment_amount: Decimal | None = Field(default=None, ge=0)
    milestones: list[str] = Field(default_factory=list)


class PricingQuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: ServiceCategory
    sub_services: list[str] = Field(default_factory=list)
    word_count: int | None = Field(default=None, gt=0)
    page_count: int | None = Field(default=None, gt=0)
    duration_minutes: int | None = Field(default=None, gt=0)
    genre: str | None = None
    tier: str | None = None
    complexity: str | None = None
    urgency: str | None = None
    add_ons: list[str] = Field(default_factory=list)
    thread_id: UUID
    customer_id: UUID | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    raw_user_request: str


class TimelineEstimateRequest(PricingQuoteRequest):
    pass


class QuoteLineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: ServiceCategory
    tier: str | None = None
    add_on: str | None = None
    price_range: MoneyRange
    timeline_range: TimelineRange | None = None
    assumptions: list[str] = Field(default_factory=list)


class PricingQuoteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote_id: UUID = Field(default_factory=uuid4)
    service: ServiceCategory
    line_items: list[QuoteLineItem] = Field(default_factory=list)
    total_price_range: MoneyRange | None = None
    total_timeline_range: TimelineRange | None = None
    currency: str = "USD"
    valid_until: datetime = Field(default_factory=lambda: datetime.now(UTC) + timedelta(days=14))
    assumptions: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    risk_flags: list[str] = Field(default_factory=list)
    human_review_required: bool = False
    suggested_phrasing: str


class TimelineEstimateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    estimate_id: UUID = Field(default_factory=uuid4)
    service: ServiceCategory
    timeline_range: TimelineRange | None = None
    earliest_start_date: datetime = Field(default_factory=lambda: datetime.now(UTC))
    assumptions: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    risk_flags: list[str] = Field(default_factory=list)
    human_review_required: bool = False
    suggested_phrasing: str


class RequiredInputsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: ServiceCategory
    tier: str | None = None
    word_count: int | None = Field(default=None, gt=0)
    page_count: int | None = Field(default=None, gt=0)
    genre: str | None = None


class RequiredInputsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: ServiceCategory
    missing_inputs: list[str] = Field(default_factory=list)
    suggested_question: str


class ExplainQuoteAssumptionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote_id: UUID
    assumptions: list[str] = Field(default_factory=list)


class ExplainQuoteAssumptionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote_id: UUID
    assumptions: list[str]
    explanation: str


def ensure_decimal(value: object, *, field_name: str) -> Decimal:
    if isinstance(value, str) and value == "REPLACE_WITH_APPROVED_VALUE":
        raise PricingConfigurationError(f"{field_name} is not approved")
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise PricingConfigurationError(f"{field_name} must be numeric") from exc
    if result < 0:
        raise PricingConfigurationError(f"{field_name} must be non-negative")
    return result


class ServiceCatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str
    required_inputs: list[str]
    default_tier: str
    tiers: list[str]

    @field_validator("required_inputs", "tiers")
    @classmethod
    def non_empty_list(cls, value: list[str]) -> list[str]:
        if not value:
            msg = "list must not be empty"
            raise ValueError(msg)
        return value


class ServiceCatalogConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    services: dict[ServiceCategory, ServiceCatalogEntry]
