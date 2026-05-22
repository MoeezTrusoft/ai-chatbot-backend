from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from .models import PricingTimelineQuote


class QuoteAuditEvent(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    quote_id: UUID
    event_type: str
    payload: dict[str, Any]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class InMemoryQuoteRepository:
    """Small repository for tests and local demos.

    Production integration should replace this with the architecture's Postgres-backed
    quote_requests, quote_results, quote_events, and pricing_audit_logs tables.
    """

    def __init__(self) -> None:
        self.quotes: dict[UUID, PricingTimelineQuote] = {}
        self.events: list[QuoteAuditEvent] = []

    def save_quote(self, quote: PricingTimelineQuote) -> None:
        self.quotes[quote.quote_id] = quote
        self.events.append(
            QuoteAuditEvent(
                quote_id=quote.quote_id,
                event_type="quote.created",
                payload={"status": quote.status, "config_versions": quote.config_versions},
            )
        )

    def get_quote(self, quote_id: UUID) -> PricingTimelineQuote | None:
        return self.quotes.get(quote_id)

    def append_event(self, quote_id: UUID, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append(
            QuoteAuditEvent(quote_id=quote_id, event_type=event_type, payload=payload)
        )
