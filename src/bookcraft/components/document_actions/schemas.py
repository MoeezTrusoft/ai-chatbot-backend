from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NDAActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: UUID | None = None
    thread_id: UUID
    lead_id: UUID | None = None
    author_title: str = "Author"
    author_full_name: str
    author_phone: str
    author_email: str
    effective_date: str
    signature: str | None = None
    send_email: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "author_title",
        "author_full_name",
        "author_phone",
        "author_email",
        "effective_date",
        "signature",
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


class NDAActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    document_id: str | None = None
    status: str
    delivery_status: str | None = None
    recipient_email: str
    html_path: str | None = None
    pdf_path: str | None = None
    provider_message_id: str | None = None
    error_code: str | None = None
    customer_safe_summary: str
    required_params: dict[str, Any] = Field(default_factory=dict)


class AgreementActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: UUID | None = None
    thread_id: UUID
    lead_id: UUID | None = None
    quote_id: UUID | None = None
    client_full_name: str
    client_phone: str
    client_email: str
    client_location: str
    effective_date: str
    signature: str | None = None
    send_email: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "client_full_name",
        "client_phone",
        "client_email",
        "client_location",
        "effective_date",
        "signature",
        mode="before",
    )
    @classmethod
    def clean_agreement_strings(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class AgreementActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    document_id: str | None = None
    quote_id: UUID | None = None
    status: str
    delivery_status: str | None = None
    recipient_email: str
    html_path: str | None = None
    pdf_path: str | None = None
    provider_message_id: str | None = None
    error_code: str | None = None
    customer_safe_summary: str
    required_params: dict[str, Any] = Field(default_factory=dict)
