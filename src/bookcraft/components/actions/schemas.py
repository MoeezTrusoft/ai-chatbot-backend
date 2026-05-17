from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ActionType(StrEnum):
    CREATE_LEAD = "create_lead"
    SCHEDULE_CONSULTATION = "schedule_consultation"
    PRICE_QUOTE = "price_quote"
    PORTFOLIO_LOOKUP = "portfolio_lookup"
    GENERATE_NDA = "generate_nda"
    GENERATE_AGREEMENT = "generate_agreement"


class ActionStatus(StrEnum):
    NOT_NEEDED = "not_needed"
    MISSING_INFO = "missing_info"
    READY = "ready"
    NEEDS_CONFIRMATION = "needs_confirmation"
    EXECUTED = "executed"
    FAILED = "failed"
    BLOCKED = "blocked"


class ActionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: ActionType | None = None
    status: ActionStatus
    missing_slots: list[str] = Field(default_factory=list)
    recommended_follow_up_slots: list[str] = Field(default_factory=list)
    collected_slots: dict[str, Any] = Field(default_factory=dict)
    confirmation_required: bool = False
    pending_confirmation_key: str | None = None
    customer_safe_prompt: str | None = None
    reason: str


class ActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: ActionType
    success: bool
    result_id: str | None = None
    customer_safe_summary: str
    internal_summary: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    duration_ms: float = 0.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
