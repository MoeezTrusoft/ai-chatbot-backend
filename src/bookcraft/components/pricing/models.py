from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ServiceCategory(StrEnum):
    GHOSTWRITING = "ghostwriting"
    EDITING_PROOFREADING = "editing_proofreading"
    COVER_DESIGN_ILLUSTRATION = "cover_design_illustration"
    INTERIOR_FORMATTING = "interior_formatting"
    PUBLISHING_DISTRIBUTION = "publishing_distribution"
    MARKETING_PROMOTION = "marketing_promotion"
    AUTHOR_WEBSITE = "author_website"
    AUDIOBOOK_PRODUCTION = "audiobook_production"
    VIDEO_TRAILER = "video_trailer"


class QuoteStatus(StrEnum):
    NEEDS_CLARIFICATION = "needs_clarification"
    ESTIMATED = "estimated"
    FORMAL_QUOTE_READY = "formal_quote_ready"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    ACCEPTED = "accepted"
    EXPIRED = "expired"


class UnitType(StrEnum):
    WORDS = "words"
    PAGES = "pages"
    FINISHED_HOURS = "finished_hours"
    VIDEO_SECONDS = "video_seconds"
    PROJECT = "project"
    ASSETS = "assets"
    CALENDAR_DAYS = "calendar_days"
    BUSINESS_DAYS = "business_days"


class Money(BaseModel):
    amount: Decimal = Field(ge=Decimal("0"))
    currency: str = "USD"

    model_config = ConfigDict(use_enum_values=True, json_encoders={Decimal: str})

    @field_validator("amount", mode="before")
    @classmethod
    def parse_amount(cls, value: Any) -> Decimal:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def __add__(self, other: Money) -> Money:
        self._assert_same_currency(other)
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        self._assert_same_currency(other)
        amount = self.amount - other.amount
        return Money(amount=max(amount, Decimal("0.00")), currency=self.currency)

    def __mul__(self, factor: Decimal | int | float | str) -> Money:
        return Money(amount=self.amount * Decimal(str(factor)), currency=self.currency)

    def _assert_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise ValueError(f"Currency mismatch: {self.currency} != {other.currency}")


class MoneyRange(BaseModel):
    low: Money
    high: Money

    @model_validator(mode="after")
    def validate_range(self) -> MoneyRange:
        self.low._assert_same_currency(self.high)
        if self.low.amount > self.high.amount:
            raise ValueError("MoneyRange.low cannot be greater than high")
        return self


class DurationRange(BaseModel):
    low: Decimal = Field(ge=Decimal("0"))
    high: Decimal = Field(ge=Decimal("0"))
    unit: Literal["business_days", "calendar_days"] = "business_days"

    @field_validator("low", "high", mode="before")
    @classmethod
    def parse_decimal(cls, value: Any) -> Decimal:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @model_validator(mode="after")
    def validate_range(self) -> DurationRange:
        if self.low > self.high:
            raise ValueError("DurationRange.low cannot be greater than high")
        return self


class FieldSource(StrEnum):
    USER_STATED = "user_stated"
    USER_CONFIRMED = "user_confirmed"
    AI_EXTRACTED = "ai_extracted"
    CSR_ENTERED = "csr_entered"
    SYSTEM = "system"


class FieldMeta(BaseModel):
    value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    source: FieldSource
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    extracted_by: str | None = None
    raw_excerpt: str | None = None


class QuoteGlobalInputs(BaseModel):
    book_title: str | None = None
    genre: str | None = None
    subgenre: str | None = None
    word_count: int | None = Field(default=None, ge=1)
    page_count: int | None = Field(default=None, ge=1)
    manuscript_status: str | None = None
    format_targets: list[str] = Field(default_factory=list)
    launch_goal: str | None = None
    author_location: str | None = None


class RequestedTimeline(BaseModel):
    duration: Decimal = Field(gt=Decimal("0"))
    unit: Literal["business_days", "calendar_days", "weeks", "months"] = "business_days"

    @field_validator("duration", mode="before")
    @classmethod
    def parse_duration(cls, value: Any) -> Decimal:
        return Decimal(str(value))

    def to_business_days(self) -> Decimal:
        if self.unit == "business_days":
            return self.duration
        if self.unit == "calendar_days":
            return self.duration * Decimal("5") / Decimal("7")
        if self.unit == "weeks":
            return self.duration * Decimal("5")
        if self.unit == "months":
            return self.duration * Decimal("21.75")
        raise ValueError(f"Unsupported timeline unit {self.unit}")


class DiscountRequest(BaseModel):
    discount_type: Literal["none", "manual", "bundle", "coupon"] = "none"
    requested_percent: Decimal | None = None
    requested_amount: Decimal | None = None
    requested_by_role: str | None = None
    reason: str | None = None


class PricingQuoteRequest(BaseModel):
    thread_id: UUID = Field(default_factory=uuid4)
    customer_id: UUID | None = None
    requested_services: list[ServiceCategory]
    service_inputs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    global_inputs: QuoteGlobalInputs = Field(default_factory=QuoteGlobalInputs)
    requested_timeline: RequestedTimeline | None = None
    discount_request: DiscountRequest | None = None
    quote_mode: Literal["estimate", "formal_quote", "agreement_ready"] = "estimate"
    field_meta_snapshot: dict[str, FieldMeta] = Field(default_factory=dict)

    model_config = ConfigDict(
        extra="forbid",
        use_enum_values=True,
        json_encoders={Decimal: str},
    )

    @field_validator("requested_services")
    @classmethod
    def must_have_services(cls, value: list[ServiceCategory]) -> list[ServiceCategory]:
        if not value:
            raise ValueError("At least one requested service is required")
        return value


class MissingInput(BaseModel):
    service: ServiceCategory
    field: str
    question: str
    severity: Literal["required", "recommended"] = "required"


class QuoteWarning(BaseModel):
    code: str
    message: str
    service: ServiceCategory | None = None
    requires_human_review: bool = False


class ComplexityContribution(BaseModel):
    driver: str
    selected_value: Any
    points: Decimal
    reason: str | None = None


class AddOnLine(BaseModel):
    code: str
    title: str
    quantity: Decimal = Decimal("1")
    price: Money
    duration_days: Decimal = Decimal("0")
    complexity_points: Decimal = Decimal("0")


class DiscountLine(BaseModel):
    code: str
    description: str
    amount: Money
    percent: Decimal | None = None
    requires_human_review: bool = False


class QuoteLineItem(BaseModel):
    service: ServiceCategory
    package_or_tier: str | None = None
    unit_type: str
    unit_quantity: Decimal
    base_price: Money
    complexity_factor: Decimal
    complexity_price: Money
    schedule_multiplier: Decimal
    rush_surcharge: Money
    add_on_total: Money
    final_price_range: MoneyRange
    base_duration_days: Decimal
    complexity_duration_days: Decimal
    final_duration_days: Decimal
    selected_add_ons: list[AddOnLine] = Field(default_factory=list)
    complexity_breakdown: list[ComplexityContribution] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[QuoteWarning] = Field(default_factory=list)
    human_review_required: bool = False
    calculation_trace: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(use_enum_values=True, json_encoders={Decimal: str})


class TimelineItem(BaseModel):
    service: ServiceCategory
    start_offset_day: Decimal
    end_offset_day: Decimal
    dependencies: list[ServiceCategory] = Field(default_factory=list)
    can_overlap: bool = False


class ProjectTimeline(BaseModel):
    total_timeline: DurationRange
    schedule: list[TimelineItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PaymentScheduleOption(BaseModel):
    code: str
    label: str
    description: str
    payments: list[dict[str, Any]]


class PricingTimelineQuote(BaseModel):
    quote_id: UUID = Field(default_factory=uuid4)
    quote_version: str = "2.2"
    config_versions: dict[str, str]
    status: QuoteStatus
    requested_services: list[ServiceCategory]
    line_items: list[QuoteLineItem] = Field(default_factory=list)
    subtotal_range: MoneyRange
    discount_lines: list[DiscountLine] = Field(default_factory=list)
    total_price_range: MoneyRange
    timeline: ProjectTimeline
    payment_schedule_options: list[PaymentScheduleOption] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    missing_inputs: list[MissingInput] = Field(default_factory=list)
    warnings: list[QuoteWarning] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    human_review_required: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = Field(default_factory=lambda: datetime.now(UTC) + timedelta(days=14))
    audit_trace: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(use_enum_values=True, json_encoders={Decimal: str})
