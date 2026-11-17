"""State and request shapes for the call-now offer flow."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from bookcraft.domain.state import CallbackFlowState

# What the bot last asked the visitor as part of the call flow.
PendingStep = Literal[
    "offer_now",            # "Are you free for a quick call right now?"
    "need_phone_now",       # free now, but no number on record yet
    "ask_time",             # "What time suits you for a call?"
    "need_phone_scheduled",  # a time is agreed, the number is still missing
]

CallbackMode = Literal["now", "scheduled"]

__all__ = ["CallbackFlowState", "CallbackMode", "CallbackRequest", "PendingStep"]


class CallbackRequest(BaseModel):
    """A callback the team must act on. Returned by the service after it is stored."""

    model_config = ConfigDict(extra="forbid")

    callback_id: str
    thread_id: UUID
    customer_id: UUID | None = None
    visit_id: str | None = None
    mode: CallbackMode
    phone: str
    name: str | None = None
    requested_time_text: str | None = None
    customer_timezone: str | None = None
    summary: str = ""
    promised_within_minutes: int | None = None
    created_at: datetime
    # False when an identical request already existed (idempotent replay).
    created: bool = True

    @property
    def phone_last4(self) -> str:
        digits = "".join(ch for ch in self.phone if ch.isdigit())
        return digits[-4:]

    def action_event(self) -> dict[str, object]:
        """The ``callback_requested`` action event returned to the Sales bridge."""
        return {
            "type": "callback_requested",
            "mode": self.mode,
            "callback_id": self.callback_id,
            "phone": self.phone,
            "phone_last4": self.phone_last4,
            "name": self.name,
            "requested_time_text": self.requested_time_text,
            "customer_timezone": self.customer_timezone,
            "summary": self.summary,
            "promised_within_minutes": self.promised_within_minutes,
        }
