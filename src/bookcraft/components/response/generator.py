from dataclasses import dataclass

from prometheus_client import Histogram

from bookcraft.components.extraction.schemas import CombinedExtraction
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.llm.metrics import LLM_CALLS
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.components.pricing.models import PricingTimelineQuote, QuoteStatus
from bookcraft.components.rag.schemas import RetrievedChunk
from bookcraft.components.response.schemas import ResponseDraft
from bookcraft.domain.enums import QueryIntentType
from bookcraft.domain.state import ThreadState

RESPONSE_SECONDS = Histogram("response_generation_seconds", "Response generation latency.")

GREETING_RESPONSE = "Hello! How can I help with your book project today?"


@dataclass(slots=True)
class SonnetResponseGenerator:
    provider_name: str = "mock_sonnet"

    async def generate(
        self,
        *,
        message: ProcessedMessage,
        state: ThreadState,
        intent: IntentVote,
        extraction: CombinedExtraction,
        rag_chunks: list[RetrievedChunk] | None = None,
        pricing_quote: PricingTimelineQuote | None = None,
        timeline_estimate: PricingTimelineQuote | None = None,
        pricing_missing_question: str | None = None,
    ) -> ResponseDraft:
        del state, extraction
        with RESPONSE_SECONDS.time():
            if (
                intent.query_primary == QueryIntentType.GREETING
                and intent.confidence >= 0.9
                and message.normalized.lower() in {"hi", "hello", "hey"}
            ):
                return ResponseDraft(text=GREETING_RESPONSE, source="deterministic_greeting")
            if pricing_missing_question:
                return ResponseDraft(text=pricing_missing_question, source="pricing_engine")
            if pricing_quote is not None:
                return ResponseDraft(
                    text=_pricing_quote_text(pricing_quote),
                    source="pricing_engine",
                )
            if timeline_estimate is not None:
                return ResponseDraft(
                    text=_timeline_quote_text(timeline_estimate),
                    source="pricing_engine",
                )
            LLM_CALLS.labels(provider=self.provider_name, purpose="response").inc()
            return ResponseDraft(
                text=self._mock_response(intent, rag_chunks or []),
                source=self.provider_name,
            )

    @staticmethod
    def _mock_response(intent: IntentVote, rag_chunks: list[RetrievedChunk]) -> str:
        quote_intents = {QueryIntentType.PRICING_QUESTION, QueryIntentType.TIMELINE_QUESTION}
        if intent.query_primary in quote_intents:
            return (
                "I can help scope that, but pricing and timelines must come from BookCraft's "
                "deterministic quote engine. To prepare that, please share your service, genre, "
                "manuscript word count or page count, and how urgent the project is."
            )
        if intent.query_primary == QueryIntentType.PORTFOLIO_REQUEST:
            return (
                "I can help route a portfolio request. Samples must come from the approved "
                "BookCraft registry, so I won't invent links here."
            )
        if intent.query_primary == QueryIntentType.NDA_REQUEST:
            return (
                "I can help start the NDA request. Legal documents must use approved templates "
                "and manual gating, so I need the author name, email, phone, book title, and date."
            )
        if intent.query_primary == QueryIntentType.AGREEMENT_REQUEST:
            return (
                "I can help start the service agreement request. Agreement text must come from "
                "the approved template, not from the language model."
            )
        if rag_chunks:
            first = rag_chunks[0]
            return f"{first.content}\n\nSource: {first.title}, section: {first.section}."
        return (
            "BookCraft can help with ghostwriting, editing, cover design, formatting, audiobook "
            "production, publishing, marketing, author websites, and video trailers. Tell me "
            "which service you are considering and what stage your manuscript is in."
        )


def _pricing_quote_text(quote: PricingTimelineQuote) -> str:
    if quote.status == QuoteStatus.NEEDS_CLARIFICATION and quote.missing_inputs:
        return quote.missing_inputs[0].question
    if any(warning.code == "VALUES_NOT_APPROVED" for warning in quote.warnings):
        return (
            "I can scope this, but BookCraft's v2.2 pricing values are not approved for "
            "customer-facing use yet. I won't guess at numbers."
        )
    return (
        f"The deterministic engine returned a {quote.total_price_range.low.currency} "
        f"{quote.total_price_range.low.amount}-{quote.total_price_range.high.amount} range "
        f"and {quote.timeline.total_timeline.low}-{quote.timeline.total_timeline.high} "
        "business days, subject to assumptions."
    )


def _timeline_quote_text(quote: PricingTimelineQuote) -> str:
    if quote.status == QuoteStatus.NEEDS_CLARIFICATION and quote.missing_inputs:
        return quote.missing_inputs[0].question
    if any(warning.code == "VALUES_NOT_APPROVED" for warning in quote.warnings):
        return (
            "I can scope this, but BookCraft's v2.2 timeline values are not approved for "
            "customer-facing use yet. I won't guess at timing."
        )
    return (
        f"The deterministic engine returned a {quote.timeline.total_timeline.low}-"
        f"{quote.timeline.total_timeline.high} business day range, subject to assumptions."
    )
