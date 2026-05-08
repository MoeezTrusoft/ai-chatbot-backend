from __future__ import annotations

from dataclasses import dataclass

from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.response.schemas import ResponseRoute
from bookcraft.domain.enums import QueryIntentType


@dataclass(slots=True)
class ResponseRouter:
    def route(self, intent: IntentVote) -> ResponseRoute:
        if intent.needs_clarification:
            return ResponseRoute(name="clarification", reason="intent_requested_clarification")
        if intent.query_primary in {
            QueryIntentType.PRICING_QUESTION,
            QueryIntentType.TIMELINE_QUESTION,
        }:
            return ResponseRoute(
                name="price_timeline",
                reason="quote_intent",
                requires_tool_output=True,
            )
        if intent.query_primary == QueryIntentType.PORTFOLIO_REQUEST:
            return ResponseRoute(
                name="portfolio",
                reason="portfolio_intent",
                requires_tool_output=True,
            )
        if intent.query_primary == QueryIntentType.NDA_REQUEST:
            return ResponseRoute(name="nda", reason="nda_intent", requires_tool_output=True)
        if intent.query_primary == QueryIntentType.AGREEMENT_REQUEST:
            return ResponseRoute(
                name="agreement",
                reason="agreement_intent",
                requires_tool_output=True,
            )
        return ResponseRoute(name="direct_answer", reason="default")
