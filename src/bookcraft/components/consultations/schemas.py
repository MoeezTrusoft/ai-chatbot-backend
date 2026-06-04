from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CSRProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    csr_id: str
    name: str
    priority_rank: int
    timezone: str = "America/Chicago"
    active: bool = True


class ConsultationActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: UUID | None = None
    lead_id: UUID | None = None
    thread_id: UUID
    name: str
    email: str | None = None
    phone: str | None = None
    services: list[str] = Field(default_factory=list)
    requested_time_text: str
    customer_timezone: str | None = None
    business_timezone: str = "America/Chicago"
    duration_minutes: int = 30
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "name",
        "email",
        "phone",
        "requested_time_text",
        "customer_timezone",
        "business_timezone",
        mode="before",
    )
    @classmethod
    def clean_strings(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @model_validator(mode="after")
    def validate_contact(self) -> ConsultationActionRequest:
        if not self.name:
            raise ValueError("consultation_requires_name")
        # Phone is preferred; email is a valid fallback for customers who cannot
        # provide a phone number (privacy concerns, compromised number, etc.).
        if not self.phone and not self.email:
            raise ValueError("consultation_requires_phone_or_email")
        if not self.customer_timezone:
            raise ValueError("consultation_requires_customer_timezone")
        if self.duration_minutes <= 0:
            raise ValueError("consultation_duration_must_be_positive")
        return self


class ConsultationActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    appointment_id: UUID
    lead_id: UUID | None = None
    csr_id: str
    csr_name: str
    priority_rank: int
    starts_at_utc: datetime
    ends_at_utc: datetime
    houston_display_time: str
    customer_display_time: str | None = None
    status: str
    customer_safe_summary: str
    metadata: dict[str, Any] = Field(default_factory=dict)
