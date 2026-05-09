import re
from dataclasses import dataclass, field
from typing import cast

import structlog
from prometheus_client import Histogram

from bookcraft.components.extraction.schemas import CombinedExtraction
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.llm.metrics import LLM_CALLS
from bookcraft.components.llm.protocols import LLMProvider
from bookcraft.components.portfolio.schemas import PortfolioResponse, PortfolioStatus
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.components.pricing.models import PricingTimelineQuote, QuoteStatus
from bookcraft.components.rag.schemas import RetrievedChunk
from bookcraft.components.response.routing import ResponseRouter
from bookcraft.components.response.schemas import GeneratedResponseText, ResponseDraft
from bookcraft.domain.enums import QueryIntentType
from bookcraft.domain.state import ThreadState

RESPONSE_SECONDS = Histogram("response_generation_seconds", "Response generation latency.")

GREETING_RESPONSE = "Hello! How can I help with your book project today?"
logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class SonnetResponseGenerator:
    provider_name: str = "mock_sonnet"
    adapter: LLMProvider | None = None
    router: ResponseRouter = field(default_factory=ResponseRouter)

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
        portfolio_response: PortfolioResponse | None = None,
        document_status_message: str | None = None,
    ) -> ResponseDraft:
        with RESPONSE_SECONDS.time():
            route = self.router.route(intent)
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
            if portfolio_response is not None:
                return _portfolio_response_text(portfolio_response)
            if document_status_message is not None:
                return ResponseDraft(text=document_status_message, source=route.name)
            LLM_CALLS.labels(provider=self.provider_name, purpose="response").inc()
            if self.adapter is not None:
                try:
                    generated = cast(
                        GeneratedResponseText,
                        await self.adapter.structured(
                            system=_response_system_prompt(),
                            user=_response_user_prompt(
                                message=message,
                                state=state,
                                intent=intent,
                                extraction=extraction,
                                rag_chunks=rag_chunks or [],
                                route_name=route.name,
                            ),
                            output_model=GeneratedResponseText,
                            purpose="response",
                        ),
                    )
                    text = _safe_generated_text(generated.text)
                    return ResponseDraft(
                        text=text,
                        source=self.provider_name,
                    )
                except Exception as exc:
                    # Fail closed to the deterministic fallback. The chatbot must remain
                    # usable without letting model/provider errors leak to customers.
                    logger.warning(
                        "response_generation_provider_failed",
                        provider=self.provider_name,
                        error=str(exc),
                    )
            return ResponseDraft(
                text=self._mock_response(intent, rag_chunks or [], route.name),
                source=route.name if route.name != "direct_answer" else self.provider_name,
            )

    @staticmethod
    def _mock_response(
        intent: IntentVote,
        rag_chunks: list[RetrievedChunk],
        route_name: str,
    ) -> str:
        del route_name
        quote_intents = {QueryIntentType.PRICING_QUESTION, QueryIntentType.TIMELINE_QUESTION}
        if intent.query_primary in quote_intents:
            return (
                "I can help scope that, but pricing and timelines must come from BookCraft's "
                "deterministic quote engine. To prepare that, please share your service, genre, "
                "manuscript word count or page count, and how urgent the project is."
            )
        if intent.query_primary == QueryIntentType.PORTFOLIO_REQUEST:
            return (
                "I can help with samples, but portfolio links must come from the approved "
                "BookCraft registry. Which service and genre should I match?"
            )
        if intent.query_primary == QueryIntentType.NDA_REQUEST:
            return (
                "I can help start the NDA request. Legal documents must use approved templates "
                "and manual gating, so I need the author name, email, phone, and date."
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


def _response_system_prompt() -> str:
    return "\n".join(
        [
            "You are BookCraft's controlled response writer.",
            'Return strict JSON only: {"text": "..."}.',
            "",
            "Hard rules:",
            "- Do not call tools.",
            "- Do not invent prices, timelines, discounts, payment terms, sample links,",
            "  legal clauses, rankings, or guarantees.",
            "- Pricing and timeline numbers may only come from the deterministic Pricing",
            "  & Timeline Engine. If no engine output is provided, ask for missing",
            "  scoping facts or say the quote engine must calculate it.",
            "- Legal documents must render from approved templates only. Never draft NDA",
            "  or agreement clauses.",
            "- Portfolio links must come from approved tool output only. If no portfolio",
            "  output is provided, ask which service/genre to match.",
            "- Keep the response concise, helpful, and specific to the user's message.",
            "- Use plain text, not Markdown tables, code fences, or raw JSON outside the",
            "  required JSON envelope.",
        ]
    )


def _response_user_prompt(
    *,
    message: ProcessedMessage,
    state: ThreadState,
    intent: IntentVote,
    extraction: CombinedExtraction,
    rag_chunks: list[RetrievedChunk],
    route_name: str,
) -> str:
    state_summary = {
        "sales_stage": state.sales_stage.value,
        "service": intent.service_primary.value if intent.service_primary else None,
        "project": {
            "genre": state.project.genre.value,
            "word_count_known": state.project.word_count.value is not None,
            "page_count_known": state.project.page_count.value is not None,
            "manuscript_status": state.project.manuscript_status.value,
        },
        "commercial": {
            "latest_quote_id": state.commercial.latest_quote_id.value,
        },
    }
    rag_context = [
        {
            "title": chunk.title,
            "section": chunk.section,
            "content": chunk.content[:900],
            "checksum": chunk.checksum,
        }
        for chunk in rag_chunks[:5]
    ]
    payload = {
        "route": route_name,
        "normalized_message": message.normalized,
        "intent": intent.model_dump(mode="json"),
        "state_summary": state_summary,
        "extraction_delta_count": len(extraction.state_deltas),
        "rag_context": rag_context,
        "instruction": (
            "Write the next assistant reply. If RAG context is present, use it for "
            "service/process explanation only. If context is absent, answer from safe "
            "BookCraft service knowledge or ask a focused clarification."
        ),
    }
    return str(payload)


def _safe_generated_text(text: str) -> str:
    stripped = text.strip()
    if _contains_forbidden_generation(stripped):
        return (
            "I can help with that, but I need to keep this answer within BookCraft's "
            "approved workflow. Tell me which service you want to focus on first."
        )
    return stripped


def _contains_forbidden_generation(text: str) -> bool:
    lowered = text.lower()
    forbidden = ["<%", "%>", "```json", "obligations of confidentiality"]
    if any(fragment in lowered for fragment in forbidden):
        return True
    if re.search(r"\$ ?\d|\busd\b|£ ?\d|€ ?\d", text, flags=re.IGNORECASE):
        return True
    if re.search(
        r"\b\d+\s*(business\s+)?(day|days|week|weeks|month|months)\b",
        text,
        flags=re.IGNORECASE,
    ):
        return True
    if re.search(r"\b\d+\s*%|\b\d+\s*percent\b", text, flags=re.IGNORECASE):
        return True
    return False


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


def _portfolio_response_text(response: PortfolioResponse) -> ResponseDraft:
    if response.status != PortfolioStatus.FOUND:
        return ResponseDraft(text=response.message, source="portfolio_engine")
    lines = [response.message]
    approved_urls: list[str] = []
    for sample in response.samples:
        link = sample.url or sample.cover_image
        if link:
            approved_urls.append(link)
            lines.append(f"- {sample.title}: {link}")
        else:
            lines.append(f"- {sample.title}")
    return ResponseDraft(
        text="\n".join(lines),
        source="portfolio_engine",
        approved_urls=approved_urls,
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
