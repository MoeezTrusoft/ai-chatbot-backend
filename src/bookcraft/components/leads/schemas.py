from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _clean_string(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


class CreateOrUpdateLeadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: UUID | None = None
    thread_id: UUID | None = None
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    preferred_contact_method: str | None = None
    services: list[str] = Field(default_factory=list)
    genre: str | None = None
    word_count: int | None = None
    page_count: int | None = None
    manuscript_status: str | None = None
    deadline: str | None = None
    source: str = "chatbot"
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "name",
        "email",
        "phone",
        "preferred_contact_method",
        "genre",
        "manuscript_status",
        "deadline",
        "source",
        "notes",
        mode="before",
    )
    @classmethod
    def _normalize_strings(cls, value: object) -> object:
        return _clean_string(value)


class LeadView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    customer_id: UUID | None = None
    thread_id: UUID | None = None
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    preferred_contact_method: str | None = None
    services: list[str] = Field(default_factory=list)
    genre: str | None = None
    word_count: int | None = None
    page_count: int | None = None
    manuscript_status: str | None = None
    deadline: str | None = None
    source: str
    status: str
    created_at: datetime
    updated_at: datetime


class LeadOperationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lead: LeadView
    created: bool
    updated_fields: list[str] = Field(default_factory=list)
