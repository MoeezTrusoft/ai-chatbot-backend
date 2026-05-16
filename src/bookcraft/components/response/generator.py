import re
from dataclasses import dataclass, field
from typing import Any, cast

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
        runtime_atoms: dict[str, Any] | None = None,
        response_hint: str | None = None,
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

            # Deterministic guard: mixed requests for pricing, samples/portfolio,
            # and NDA must not rely on the response LLM when required scope is
            # missing. This keeps links, numbers, and legal/document language
            # inside approved tools/templates.
            if intent.query_primary == QueryIntentType.PORTFOLIO_REQUEST:
                return ResponseDraft(
                    text=(
                        "I can help with samples, pricing, and the NDA safely. "
                        "To avoid inventing links or numbers, BookCraft needs the service type "
                        "and genre before showing portfolio samples; word or page count, "
                        "manuscript status, and deadline before pricing or timelines; and "
                        "author name, email, phone, and effective date before an NDA can enter "
                        "the document queue."
                    ),
                    source="deterministic_mixed_request_guard",
                )

            # Fast path: RAG-backed service/help responses already have approved
            # source text. Avoid spending 6-8 seconds on Sonnet just to restate
            # the retrieved BookCraft content. High-stakes flows above still use
            # deterministic pricing/document/portfolio guards.
            if rag_chunks:
                return ResponseDraft(
                    text=_humanized_template_response(
                        intent=intent,
                        state=state,
                        message=message,
                        runtime_atoms=runtime_atoms or {},
                        rag_chunks=rag_chunks,
                        route_name=route.name,
                        response_hint=response_hint,
                    ),
                    source=route.name if route.name != "direct_answer" else "rag_fast_path",
                )

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
                                runtime_atoms=runtime_atoms or {},
                                response_hint=response_hint,
                            ),
                            output_model=GeneratedResponseText,
                            purpose="response",
                        ),
                    )
                    text = _safe_generated_text(
                        generated.text,
                        fallback=_humanized_template_response(
                            intent=intent,
                            state=state,
                            message=message,
                            runtime_atoms=runtime_atoms or {},
                            rag_chunks=rag_chunks or [],
                            route_name=route.name,
                            response_hint=response_hint,
                        ),
                    )
                    return ResponseDraft(text=text, source=self.provider_name)
                except Exception as exc:
                    # Fail closed to the deterministic fallback. The chatbot must remain
                    # usable without letting model/provider errors leak to customers.
                    logger.warning(
                        "response_generation_provider_failed",
                        provider=self.provider_name,
                        error=str(exc),
                    )
            return ResponseDraft(
                text=_humanized_template_response(
                    intent=intent,
                    state=state,
                    message=message,
                    runtime_atoms=runtime_atoms or {},
                    rag_chunks=rag_chunks or [],
                    route_name=route.name,
                    response_hint=response_hint,
                ),
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
        if intent.query_primary == QueryIntentType.CONSULTATION_REQUEST:
            return (
                "A human consultant should review the service mix, missing scope fields, "
                "pricing readiness, NDA requirements, agreement readiness, and production "
                "planning next. Before pricing, NDA, agreement, or production planning can "
                "move forward, BookCraft still needs the exact services, manuscript status, "
                "word or page count, genre, deadline, contact details, and any approved "
                "quote or document requirements."
            )
        if rag_chunks:
            first = rag_chunks[0]
            return f"{first.content}\n\nSource: {first.title}, section: {first.section}."
        return (
            "BookCraft can help with ghostwriting, editing, cover design, formatting, audiobook "
            "production, publishing, marketing, author websites, and video trailers. Tell me "
            "which service you are considering and what stage your manuscript is in."
        )


def _humanized_template_response(
    *,
    intent: IntentVote,
    state: ThreadState,
    message: ProcessedMessage,
    runtime_atoms: dict[str, Any],
    rag_chunks: list[RetrievedChunk],
    route_name: str,
    response_hint: str | None = None,
) -> str:
    del message, rag_chunks, route_name

    services = _ordered_human_services(intent, runtime_atoms)
    service_phrase = _service_phrase(services)
    cta = _cta_for_intent(intent, runtime_atoms, state)

    if response_hint == "repeat_message":
        return (
            f"Same scope as your last message — {service_phrase}. "
            "To move this forward, I still need the key project details instead of "
            f"repeating the service overview. {cta}"
        )

    if intent.query_primary == QueryIntentType.READY_TO_BUY:
        return (
            f"Great — {service_phrase} sounds like the right working scope for this "
            "project. Since you already have a drafted manuscript, the clean order is "
            "to confirm the editorial stage first, then move into production and "
            f"publishing once the text is stable. {cta}"
        )

    if intent.query_primary == QueryIntentType.NDA_REQUEST:
        return (
            "Absolutely — we can keep confidentiality clear before you share sensitive "
            f"manuscript details. I also noted the service fit as {service_phrase}, so "
            f"once the NDA is started we can scope that work safely. {cta}"
        )

    if intent.query_primary in {
        QueryIntentType.PRICING_QUESTION,
        QueryIntentType.TIMELINE_QUESTION,
    }:
        return (
            f"I can help scope {service_phrase}, but I should not guess prices or "
            "timelines. Those need to come from BookCraft’s approved quote engine "
            f"once the project details are complete. {cta}"
        )

    if intent.query_primary == QueryIntentType.PORTFOLIO_REQUEST:
        return (
            f"Yes — I can help match samples to {service_phrase}. The useful way to "
            "do that is by matching the service type, genre, and book category "
            f"instead of sending random examples. {cta}"
        )

    if intent.query_primary == QueryIntentType.MANUSCRIPT_STATUS_UPDATE:
        return (
            f"That helps — based on what you shared, {service_phrase} is probably "
            "the right starting point. If the book is still at idea or rough-draft "
            f"stage, the first step is clarifying creation versus polishing. {cta}"
        )

    if intent.query_primary == QueryIntentType.CONSULTATION_REQUEST:
        return (
            f"Based on your message, {service_phrase} should be reviewed together "
            "rather than as separate pieces. A consultant-style scope will help avoid "
            f"quoting the wrong service or skipping an important production step. {cta}"
        )

    return (
        f"Got it — {service_phrase} is the scope I’m seeing from your message. "
        "The best next step is to clarify the manuscript stage and project basics "
        f"so BookCraft can guide you without guessing. {cta}"
    )


def _ordered_human_services(intent: IntentVote, runtime_atoms: dict[str, Any]) -> list[str]:
    raw_services: list[str] = []

    runtime_services = runtime_atoms.get("services", [])
    if isinstance(runtime_services, list):
        raw_services.extend(value for value in runtime_services if isinstance(value, str))

    if intent.service_primary is not None:
        raw_services.append(intent.service_primary.value)

    raw_services.extend(service.value for service in intent.service_secondary)

    negated_raw = runtime_atoms.get("negated_services", [])
    negated = (
        {value for value in negated_raw if isinstance(value, str)}
        if isinstance(negated_raw, list)
        else set()
    )

    seen: set[str] = set()
    ordered: list[str] = []
    for service in raw_services:
        if service in seen or service in negated:
            continue
        seen.add(service)
        ordered.append(service)

    if not ordered:
        return ["your book project"]

    return [_human_service_name(service) for service in ordered]


def _human_service_name(service: str) -> str:
    names = {
        "ghostwriting": "ghostwriting",
        "editing_proofreading": "editing and proofreading",
        "cover_design_illustration": "cover design and illustration",
        "interior_formatting": "interior formatting",
        "publishing_distribution": "publishing and distribution",
        "marketing_promotion": "marketing and promotion",
        "audiobook_production": "audiobook production",
        "author_website": "author website",
        "video_trailer": "video trailer",
    }
    return names.get(service, service.replace("_", " "))


def _service_phrase(services: list[str]) -> str:
    if not services:
        return "your book project"
    if len(services) == 1:
        return services[0]
    if len(services) == 2:
        return f"{services[0]} and {services[1]}"
    return f"{', '.join(services[:-1])}, and {services[-1]}"


def _cta_for_intent(
    intent: IntentVote,
    runtime_atoms: dict[str, Any],
    state: ThreadState,
) -> str:
    del state

    has_word_count = bool(runtime_atoms.get("word_counts"))

    if intent.query_primary == QueryIntentType.NDA_REQUEST:
        return (
            "Should I start the NDA queue if you share the author name, email, "
            "phone, and effective date?"
        )

    if intent.query_primary == QueryIntentType.PORTFOLIO_REQUEST:
        return "Which service and genre should I match the samples against?"

    if intent.query_primary in {
        QueryIntentType.PRICING_QUESTION,
        QueryIntentType.TIMELINE_QUESTION,
    }:
        if has_word_count:
            return "What manuscript stage, genre, and deadline should I use?"
        return "What word count or page count, genre, manuscript stage, and deadline should I use?"

    if intent.query_primary == QueryIntentType.READY_TO_BUY:
        return "What launch date are you aiming for, and should I scope the full bundle together?"

    return (
        "Want me to scope these together if you share the word count, genre, "
        "manuscript stage, and target launch window?"
    )


def _contains_doc_artifacts(text: str) -> bool:
    patterns = [
        r"^\s*#{1,6}\s",
        r"\n\s*\|.*\|",
        r"\bSource:\s*",
        r"##\s*Related Services",
        r"##\s*Service Tiers",
        r"###\s*Cover layouts",
        r"approved registry samples only",
        r"This is a .*stage conversation",
        r"Pricing tiers and rates are maintained",
    ]
    return any(re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE) for pattern in patterns)


def _has_human_opener(text: str) -> bool:
    head = text.lstrip()[:40]
    if not head:
        return False
    if not head[0].isupper():
        return False
    return not head.startswith(("|", "-", "#", "*", "```", ">"))


def _clean_customer_text(text: str) -> str:
    cleaned = re.sub(r"\bSource:\s*[^\n]+", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*#{1,6}\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\n\s*\|.*\|", "", cleaned)
    return cleaned.strip()


def _response_system_prompt() -> str:
    return "\n".join(
        [
            "You are a senior BookCraft project consultant.",
            "Help authors choose the right services and move toward a quote, "
            "sample request, NDA, or consultation.",
            'Return strict JSON only: {"text": "..."}',
            "",
            "Voice:",
            "- Warm, plain-spoken, concise, and consultative.",
            "- Use first person naturally: I, we, BookCraft.",
            "- Mirror all services the lead mentioned.",
            "- Acknowledge deadline, NDA, genre, manuscript status, platform, or urgency.",
            "- End with one clear next-step question.",
            "- Do not use headings, markdown tables, source labels, or copied KB sections.",
            "",
            "Hard rules:",
            "- Do not call tools.",
            "- Do not invent prices, timelines, sample links, legal clauses, or guarantees.",
            "- Pricing and timelines must come from the deterministic quote engine.",
            "- Legal documents must render from approved templates only.",
            "- Portfolio links must come from approved tool output only.",
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
    runtime_atoms: dict[str, Any],
    response_hint: str | None = None,
) -> str:
    state_summary = {
        "sales_stage": state.sales_stage.value,
        "services": {
            "primary": intent.service_primary.value if intent.service_primary else None,
            "secondary": [service.value for service in intent.service_secondary],
            "runtime_detected": runtime_atoms.get("services", []),
            "negated": runtime_atoms.get("negated_services", []),
        },
        "query": {
            "primary": intent.query_primary.value,
            "secondary": [query.value for query in intent.query_secondary],
            "runtime_cues": runtime_atoms.get("query_cues", []),
        },
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
            "Write the next assistant reply as a human BookCraft sales consultant. "
            "Use RAG only as private grounding. Do not quote it, expose source labels, "
            "or use tables/headings. Mirror all detected services and end with one "
            "clear next-step question."
        ),
        "response_hint": response_hint,
        "runtime_atoms": runtime_atoms,
    }
    return str(payload)


def _safe_generated_text(text: str, *, fallback: str) -> str:
    stripped = _clean_customer_text(text.strip())
    if (
        _contains_forbidden_generation(stripped)
        or _contains_doc_artifacts(stripped)
        or not _has_human_opener(stripped)
    ):
        return fallback
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
    if _contains_doc_artifacts(text):
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
