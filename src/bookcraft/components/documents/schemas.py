from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DocumentKind(StrEnum):
    NDA = "nda"
    AGREEMENT = "agreement"


class DocumentStatus(StrEnum):
    GENERATED = "generated"
    VERIFIED = "verified"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    REJECTED = "rejected"


class TemplateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: DocumentKind
    version: str
    path: Path
    checksum: str


class ServiceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    description: str = Field(min_length=1)


class SelectedService(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    items: list[ServiceItem] = Field(default_factory=list, min_length=1)


class NDAParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str = Field(min_length=1)
    author_title: str = Field(alias="authorTitle", min_length=1)
    author_full_name: str = Field(alias="authorFullName", min_length=1)
    author_phone: str = Field(alias="authorPhone", min_length=5)
    author_email: str = Field(alias="authorEmail")
    signature: str = Field(min_length=1)

    @field_validator("author_email")
    @classmethod
    def validate_author_email(cls, value: str) -> str:
        return _validate_email(value)


class AgreementParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logo_path: str = Field(alias="logoPath", default="")
    effective_date: str = Field(alias="effectiveDate", min_length=1)
    abbreviation: str = Field(min_length=1)
    client_full_name: str = Field(alias="clientFullName", min_length=1)
    client_phone: str = Field(alias="clientPhone", min_length=5)
    client_email: str = Field(alias="clientEmail")
    client_location: str = Field(alias="clientLocation", min_length=1)
    filtered_services: list[SelectedService] = Field(alias="filteredServices", min_length=1)
    final_fee: str = Field(alias="finalFee", min_length=1)
    total_fee: str = Field(alias="totalFee", min_length=1)
    discount_percent: int = Field(alias="discountPercent", default=0, ge=0)
    schedule_type: str = Field(alias="scheduleType", min_length=1)
    initial_percentage: int = Field(alias="initialPercentage", default=0, ge=0)
    remaining_percentage: int = Field(alias="remainingPercentage", default=0, ge=0)
    number_of_months: int = Field(alias="numberOfMonths", default=0, ge=0)
    installment_amount: str = Field(alias="installmentAmount", default="0")
    initial_amount: str = Field(alias="initialAmount", default="0")
    remaining_amount: str = Field(alias="remainingAmount", default="0")
    advance_percentage: int = Field(alias="advancePercentage", default=0, ge=0)
    final_percentage: int = Field(alias="finalPercentage", default=0, ge=0)
    before_or_after: bool = Field(alias="beforeOrAfter", default=True)
    final_milestone_service: str = Field(alias="finalMilestoneService", default="")
    milestones: list[dict[str, object]] = Field(default_factory=list)
    signature: str = Field(min_length=1)
    agreement_date: str = Field(alias="agreementDate", min_length=1)

    @field_validator("final_fee", "total_fee")
    @classmethod
    def require_engine_sourced_amount_string(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("fee fields must be provided from deterministic quote output")
        return value

    @field_validator("client_email")
    @classmethod
    def validate_client_email(cls, value: str) -> str:
        return _validate_email(value)


class DocumentGenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    kind: DocumentKind
    status: DocumentStatus
    template_version: str
    parameter_hash: str
    rendered_hash: str
    html_path: str | None = None
    pdf_path: str | None = None
    verification_errors: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    human_review_required: bool = False


class DocumentToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    params: dict[str, object]


class DocumentToolOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: DocumentGenerationResult


def _validate_email(value: str) -> str:
    if "@" not in value or "." not in value.rsplit("@", maxsplit=1)[-1]:
        raise ValueError("valid email address required")
    return value
