from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PricingActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: UUID | None = None
    thread_id: UUID
    lead_id: UUID | None = None
    services: list[str]
    collected_slots: dict[str, Any] = Field(default_factory=dict)
    use_default_assumptions: bool = False


class PricingActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote_id: UUID
    status: str
    services: list[str]
    missing_fields: list[str] = Field(default_factory=list)
    used_default_assumptions: bool = False
    assumptions: dict[str, Any] | None = None
    customer_safe_summary: str
    quote_output: dict[str, Any]
