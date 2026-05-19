import json
import re
from dataclasses import dataclass, field
from typing import Any, cast

import structlog
from prometheus_client import Histogram

from bookcraft.components.context.schemas import ContextPack
from bookcraft.components.extraction.schemas import CombinedExtraction
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.llm.metrics import LLM_CALLS
from bookcraft.components.llm.protocols import LLMProvider
from bookcraft.components.portfolio.schemas import PortfolioResponse, PortfolioStatus
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.components.pricing.models import PricingTimelineQuote, QuoteStatus
from bookcraft.components.rag.schemas import RetrievedChunk
from bookcraft.components.response.planner import ResponsePlan
from bookcraft.components.response.quality_gate import ResponseQualityReport
from bookcraft.components.response.routing import ResponseRouter
from bookcraft.components.response.schemas import GeneratedResponseText, ResponseDraft
from bookcraft.components.response.style_policy import ResponseStylePolicy
from bookcraft.components.tools.governance import ToolGovernanceDecision
from bookcraft.domain.enums import QueryIntentType
from bookcraft.domain.state import ThreadState

RESPONSE_SECONDS = Histogram("response_generation_seconds", "Response generation latency.")

GREETING_RESPONSE = "Hello! How can I help with your book project today?"
logger = structlog.get_logger(__name__)

# Module-level style policy used to build the LLM system prompt.
_STYLE_POLICY = ResponseStylePolicy.default()


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
        context_pack: ContextPack | None = None,
        response_plan: ResponsePlan | None = None,
    ) -> ResponseDraft:
        with RESPONSE_SECONDS.time():
            route = self.router.route(intent)
            rag_chunks = rag_chunks or []
            runtime_atoms = runtime_atoms or {}

            if (
                intent.query_primary == QueryIntentType.GREETING
                and intent.confidence >= 0.9
                and message.normalized.lower() in {"hi", "hello", "hey"}
            ):
                if self.adapter is None:
                    return ResponseDraft(text=GREETING_RESPONSE, source="deterministic_greeting")

            if pricing_missing_question:
                return ResponseDraft(
                    text=_customer_safe_missing_scope_question(
                        pricing_missing_question,
                        intent=intent,
                        runtime_atoms=runtime_atoms,
                    ),
                    source="pricing_engine",
                )

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
                return ResponseDraft(
                    text=_clean_guarded_status_message(
                        document_status_message,
                        intent=intent,
                        runtime_atoms=runtime_atoms,
                    ),
                    source=route.name,
                )

            # Guarded mixed request: keep legal/link/price safety, but use human copy
            # only when no LLM adapter is available.
            if intent.query_primary == QueryIntentType.PORTFOLIO_REQUEST and self.adapter is None:
                return ResponseDraft(
                    text=(
                        "I can help with samples, an estimate, and the NDA step without "
                        "guessing or sending anything generic. For samples, I’d match by "
                        "service and genre; for an estimate, I’d need word or page count, "
                        "manuscript stage, and deadline; and for the NDA, I’d need the author "
                        "name, email, phone, and preferred effective date. Which part would "
                        "you like to start with?"
                    ),
                    source="deterministic_mixed_request_guard",
                )

            template_fallback = _humanized_template_response(
                intent=intent,
                state=state,
                message=message,
                runtime_atoms=runtime_atoms,
                rag_chunks=rag_chunks,
                route_name=route.name,
                response_hint=response_hint,
                context_pack=context_pack,
                response_plan=response_plan,
            )

            if self.adapter is None:
                return ResponseDraft(text=template_fallback, source="template_no_adapter")

            text = await self._try_llm(
                message=message,
                state=state,
                intent=intent,
                extraction=extraction,
                rag_chunks=rag_chunks[:5],
                route_name=route.name,
                runtime_atoms=runtime_atoms,
                response_hint=response_hint,
                context_pack=context_pack,
                response_plan=response_plan,
                attempt="full",
            )
            if text is not None:
                return ResponseDraft(text=text, source=self.provider_name)

            text = await self._try_llm(
                message=message,
                state=state,
                intent=intent,
                extraction=extraction,
                rag_chunks=[],
                route_name=route.name,
                runtime_atoms=runtime_atoms,
                response_hint=response_hint,
                context_pack=context_pack,
                response_plan=response_plan,
                attempt="reduced",
            )
            if text is not None:
                return ResponseDraft(text=text, source=f"{self.provider_name}_reduced")

            return ResponseDraft(
                text=template_fallback,
                source=route.name if route.name != "direct_answer" else self.provider_name,
            )

    async def repair(
        self,
        *,
        bad_text: str,
        quality_report: ResponseQualityReport,
        response_plan: ResponsePlan,
        context_pack: ContextPack,
        tool_governance: ToolGovernanceDecision | None = None,
        response_hint: str | None = None,
    ) -> ResponseDraft:
        if self.adapter is None:
            return ResponseDraft(
                text=bad_text,
                source="template_no_adapter_repair_unavailable",
            )

        LLM_CALLS.labels(provider=self.provider_name, purpose="response_repair").inc()
        try:
            generated = cast(
                GeneratedResponseText,
                await self.adapter.structured(
                    system=_response_repair_system_prompt(
                        active_service=(
                            context_pack.active_service if context_pack is not None else None
                        )
                    ),
                    user=_response_repair_user_prompt(
                        bad_text=bad_text,
                        quality_report=quality_report,
                        response_plan=response_plan,
                        context_pack=context_pack,
                        tool_governance=tool_governance,
                        response_hint=response_hint,
                    ),
                    output_model=GeneratedResponseText,
                    purpose="response_repair",
                ),
            )
        except Exception as exc:
            logger.warning(
                "response_repair_provider_failed",
                provider=self.provider_name,
                error=str(exc),
            )
            return ResponseDraft(
                text=bad_text,
                source="template_no_adapter_repair_unavailable",
            )

        cleaned = _safe_generated_text(generated.text)
        if cleaned is None:
            return ResponseDraft(
                text=bad_text,
                source="template_no_adapter_repair_unavailable",
            )

        return ResponseDraft(text=cleaned, source=f"{self.provider_name}_repair")

    async def _try_llm(
        self,
        *,
        message: ProcessedMessage,
        state: ThreadState,
        intent: IntentVote,
        extraction: CombinedExtraction,
        rag_chunks: list[RetrievedChunk],
        route_name: str,
        runtime_atoms: dict[str, Any],
        response_hint: str | None,
        context_pack: ContextPack | None,
        response_plan: ResponsePlan | None,
        attempt: str,
    ) -> str | None:
        assert self.adapter is not None

        LLM_CALLS.labels(provider=self.provider_name, purpose=f"response_{attempt}").inc()

        try:
            generated = cast(
                GeneratedResponseText,
                await self.adapter.structured(
                    system=_response_system_prompt(
                        active_service=context_pack.active_service
                        if context_pack is not None
                        else None
                    ),
                    user=_response_user_prompt(
                        message=message,
                        state=state,
                        intent=intent,
                        extraction=extraction,
                        rag_chunks=rag_chunks,
                        route_name=route_name,
                        runtime_atoms=runtime_atoms,
                        response_hint=response_hint,
                        context_pack=context_pack,
                        response_plan=response_plan,
                    ),
                    output_model=GeneratedResponseText,
                    purpose=f"response_{attempt}",
                ),
            )
        except Exception as exc:
            logger.warning(
                "response_generation_provider_failed",
                provider=self.provider_name,
                attempt=attempt,
                error=str(exc),
            )
            return None

        cleaned = _safe_generated_text(generated.text)
        if cleaned is None:
            logger.info(
                "response_generation_validation_rejected",
                provider=self.provider_name,
                attempt=attempt,
                preview=generated.text[:120],
            )
        return cleaned

    @staticmethod
    def _mock_response(
        intent: IntentVote,
        rag_chunks: list[RetrievedChunk],
        route_name: str,
    ) -> str:
        del rag_chunks, route_name

        if intent.query_primary in {
            QueryIntentType.PRICING_QUESTION,
            QueryIntentType.TIMELINE_QUESTION,
        }:
            return (
                "I can help prepare a realistic estimate, but I don’t want to guess. "
                "Please share the service mix, genre, manuscript word or page count, "
                "manuscript stage, and deadline so the quote can be scoped properly."
            )

        if intent.query_primary == QueryIntentType.PORTFOLIO_REQUEST:
            return (
                "Yes — I can help match samples to your project. Which service and "
                "genre should I use so the examples are actually relevant?"
            )

        if intent.query_primary == QueryIntentType.NDA_REQUEST:
            return (
                "Absolutely — confidentiality should be clear before you share the "
                "manuscript. Would you like to start the NDA step by sharing the "
                "author name, email, phone, and preferred effective date?"
            )

        if intent.query_primary == QueryIntentType.AGREEMENT_REQUEST:
            return (
                "I can help start the agreement step once the service scope is clear. "
                "Which services should the agreement cover?"
            )

        if intent.query_primary == QueryIntentType.CONSULTATION_REQUEST:
            return (
                "A consultation makes sense here because the service mix needs to be "
                "scoped carefully. What manuscript stage, genre, deadline, and services "
                "should we review first?"
            )

        return (
            "I can help with the book project. What stage is the manuscript in, and "
            "which support do you need most right now: writing, editing, design, "
            "formatting, publishing, or marketing?"
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
    context_pack: ContextPack | None = None,
    response_plan: ResponsePlan | None = None,
) -> str:
    del message, rag_chunks, route_name

    services = _ordered_human_services(intent, runtime_atoms)
    service_phrase = _service_phrase(services)
    cta = _cta_for_intent(
        intent, runtime_atoms, state, context_pack=context_pack, response_plan=response_plan
    )

    forbid_markers = runtime_atoms.get("forbid_markers", [])
    has_guarantee_pressure = isinstance(forbid_markers, list) and "guarantee" in {
        str(item) for item in forbid_markers
    }

    if has_guarantee_pressure or intent.query_primary.value == "complaint_or_objection":
        return (
            "I wouldn’t want to promise a bestseller rank or a fixed sales number, "
            "because that would not be honest. What BookCraft can do is build a "
            f"realistic plan around {service_phrase}: positioning, publishing setup, "
            "launch assets, and promotion steps that give the book a stronger chance. "
            "Would you like me to scope a practical launch plan instead of a guarantee?"
        )

    if response_hint == "repeat_message":
        return (
            f"I’m with you — the project still looks like {service_phrase}. "
            "Rather than repeat the same overview, the useful next step is to pin down "
            f"the missing project details. {cta}"
        )

    if intent.query_primary == QueryIntentType.READY_TO_BUY:
        return (
            f"Perfect — for {service_phrase}, you’re already close to a proper project "
            "scope because you’ve shared the manuscript stage and category. I’d start "
            "by confirming the file condition, deadline, and which services should be "
            f"quoted together. {cta}"
        )

    if intent.query_primary == QueryIntentType.NDA_REQUEST:
        return (
            "Absolutely — confidentiality should be clear before you share sensitive "
            f"manuscript material. Once that’s handled, we can safely scope {service_phrase} "
            f"around the memoir. {cta}"
        )

    if intent.query_primary in {
        QueryIntentType.PRICING_QUESTION,
        QueryIntentType.TIMELINE_QUESTION,
    }:
        return (
            f"I can help you get a realistic estimate for {service_phrase}, but I don’t "
            "want to guess and give you something inaccurate. The estimate depends on "
            f"the manuscript condition, word or page count, genre, and deadline. {cta}"
        )

    if intent.query_primary == QueryIntentType.PORTFOLIO_REQUEST:
        return (
            f"Yes — for {service_phrase}, the best samples are the ones closest to your "
            "genre and project type, not random examples. For a better match, I’d narrow "
            f"them by service, book category, and style. {cta}"
        )

    if intent.query_primary == QueryIntentType.MANUSCRIPT_STATUS_UPDATE:
        return (
            f"That helps. Based on what you shared, {service_phrase} may be the right "
            "direction, but the first decision is whether you need creation, restructuring, "
            f"or polishing. {cta}"
        )

    if intent.query_primary == QueryIntentType.CONSULTATION_REQUEST:
        return (
            f"For {service_phrase}, I’d treat this as one connected book-production plan "
            "instead of separate tasks. That keeps editing, design, formatting, publishing, "
            f"and launch support from happening out of order. {cta}"
        )

    return (
        f"Thanks — based on your message, {service_phrase} is the main direction. "
        "I’d first confirm what stage the manuscript is in, then map the right services "
        f"around that instead of giving a generic recommendation. {cta}"
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
    *,
    context_pack: ContextPack | None = None,
    response_plan: ResponsePlan | None = None,
) -> str:
    # ResponsePlan.next_question overrides all other CTA logic when set.
    if response_plan is not None and response_plan.next_question is not None:
        nq = response_plan.next_question
        if context_pack is not None:
            mapped = _question_for_missing_fact(nq, context_pack=context_pack)
            if mapped is not None:
                return mapped
        return nq

    if context_pack is not None:
        for missing_fact in context_pack.allowed_next_questions:
            question = _question_for_missing_fact(
                missing_fact,
                context_pack=context_pack,
            )
            if question is not None:
                return question

    has_word_count = (
        bool(runtime_atoms.get("word_counts")) or state.project.word_count.value is not None
    )
    has_page_count = (
        bool(runtime_atoms.get("page_counts")) or state.project.page_count.value is not None
    )
    has_length = has_word_count or has_page_count
    has_genre = bool(getattr(state.project.genre, "value", None))
    has_stage = bool(getattr(state.project.manuscript_status, "value", None))

    if intent.query_primary == QueryIntentType.NDA_REQUEST:
        return (
            "Would you like me to help start the NDA step if you share the author "
            "name, email, phone, and preferred effective date?"
        )

    if intent.query_primary == QueryIntentType.PORTFOLIO_REQUEST:
        if has_genre:
            return "What cover style or visual direction should I match the samples against?"
        return "Which genre or book category should I match the samples against?"

    if intent.query_primary in {
        QueryIntentType.PRICING_QUESTION,
        QueryIntentType.TIMELINE_QUESTION,
    }:
        pricing_missing_fields: list[str] = []
        if not has_length:
            pricing_missing_fields.append("word count or page count")
        if not has_genre:
            pricing_missing_fields.append("genre")
        if not has_stage:
            pricing_missing_fields.append("manuscript stage")
        pricing_missing_fields.append("deadline")

        if len(pricing_missing_fields) == 1:
            return f"What {pricing_missing_fields[0]} should I use for the estimate?"

        return (
            f"What {', '.join(pricing_missing_fields[:-1])}, and "
            f"{pricing_missing_fields[-1]} should I use for the estimate?"
        )

    if intent.query_primary == QueryIntentType.READY_TO_BUY:
        return (
            "What launch date are you aiming for, and do you want the full service "
            "bundle scoped together?"
        )

    general_missing_fields: list[str] = []
    if not has_length:
        general_missing_fields.append("rough word count or page count")
    if not has_genre:
        general_missing_fields.append("genre")
    if not has_stage:
        general_missing_fields.append("manuscript stage")

    if general_missing_fields:
        if len(general_missing_fields) == 1:
            return (
                f"Can you share the {general_missing_fields[0]} "
                "so I can guide the next step properly?"
            )
        return (
            f"Can you share the {', '.join(general_missing_fields[:-1])}, and "
            f"{general_missing_fields[-1]} so I can guide the next step properly?"
        )

    return (
        "Since the basics are clear, would you like to move toward a cover-design "
        "scope, a quote, or a consultation?"
    )


def _question_for_missing_fact(
    missing_fact: str,
    *,
    context_pack: ContextPack,
) -> str | None:
    questions = {
        "cover_style": "What cover style or visual direction should I use for the design scope?",
        "word_or_page_count": "What rough word count or page count should I use?",
        "deadline": "What deadline or launch window should I use?",
        "genre": "What genre or book category should I use?",
        "manuscript_stage": "What manuscript stage should I use?",
    }
    question = questions.get(missing_fact)
    if question is None:
        return None

    lowered = question.casefold()
    if any(marker.casefold() in lowered for marker in context_pack.disallowed_next_questions):
        return None
    return question


def _contains_doc_artifacts(text: str) -> bool:
    patterns = [
        r"\bquote engine\b",
        r"\bpricing engine\b",
        r"\bapproved engine\b",
        r"\bdeterministic\b",
        r"\bNDA queue\b",
        r"\bdocument queue\b",
        r"\bapproved template\b",
        r"\bapproved tool\b",
        r"\btool output\b",
        r"\bRAG\b",
        r"\bruntime atoms\b",
        r"\bprovider votes\b",
        r"\bclassifier\b",
        r"\bbackend\b",
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


def _response_system_prompt(active_service: str | None = None) -> str:
    style = _STYLE_POLICY.style_instructions(active_service=active_service)
    return (
        "You are a senior BookCraft project consultant talking with an author "
        "who is considering BookCraft for their book.\n\n"
        "Your job is to help them get clarity and move one concrete step closer "
        "to a quote, sample request, NDA, or consultation.\n\n"
        f"{style}\n\n"
        "What you must NOT do:\n"
        "- Do not invent prices, timelines, sample links, legal clauses, or guarantees. "
        "If you do not have an approved number, say we should scope it together.\n"
        "- Do not ask again for facts already listed under "
        "'What we already know about the project'. "
        "If manuscript status is already known, do not ask whether they have "
        "a draft or are starting from scratch. "
        "If genre is already known, do not ask for genre again.\n"
        "- Do not use markdown headings, tables, bullet lists, or Source labels.\n"
        "- Do not say: is the scope I am seeing, BookCraft cannot show, deterministic "
        "engine, approved engine, quote engine, document queue, tool output, backend, "
        "classifier, provider votes, or runtime atoms.\n\n"
        "RAG context, if provided, is private grounding only. Use it to inform your "
        "reply, but do not quote it, summarize it back, cite it, or copy its structure.\n\n"
        'Output protocol: respond with one JSON object: {"text": "your reply"} '
        "and nothing else. The text field is plain prose, no markdown."
    )


def _response_repair_system_prompt(active_service: str | None = None) -> str:
    style = _STYLE_POLICY.style_instructions(active_service=active_service)
    return (
        "You are repairing a customer-facing BookCraft assistant response. "
        "Use only the facts and guidance provided below, and write a clean reply "
        "that the customer would actually receive.\n\n"
        f"{style}\n\n"
        "Do not use backend, classifier, runtime atoms, provider votes, RAG, tool_governance, "
        "action_plan, deterministic engine, quote engine, Source:, Context:, or Action plan:.\n"
        "Do not quote the original system or prompt. "
        "Do not mention internal labels or trace data.\n"
        'Output protocol: respond with one JSON object: {"text": "your reply"} and nothing else.'
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
    context_pack: ContextPack | None = None,
    response_plan: ResponsePlan | None = None,
) -> str:
    del extraction, route_name

    services = _ordered_human_services(intent, runtime_atoms)
    service_phrase = _service_phrase(services) if services else "their book project"

    known: list[str] = []
    if getattr(state.project.genre, "value", None):
        known.append(f"genre: {state.project.genre.value}")
    if state.project.word_count.value is not None:
        known.append(f"word count: {state.project.word_count.value}")
    if state.project.page_count.value is not None:
        known.append(f"page count: {state.project.page_count.value}")
    if getattr(state.project.manuscript_status, "value", None):
        known.append(f"manuscript status: {state.project.manuscript_status.value}")
    known_str = "; ".join(known) if known else "nothing confirmed yet"

    missing: list[str] = []
    if state.project.word_count.value is None and state.project.page_count.value is None:
        missing.append("word or page count")
    if not getattr(state.project.genre, "value", None):
        missing.append("genre")
    if not getattr(state.project.manuscript_status, "value", None):
        missing.append("manuscript stage")
    missing_str = ", ".join(missing) if missing else "no major basics missing"

    intent_label = intent.query_primary.value.replace("_", " ")

    negated = runtime_atoms.get("negated_services") or []
    negated_str = (
        f"\nThey explicitly do NOT want: {', '.join(str(item) for item in negated)}."
        if isinstance(negated, list) and negated
        else ""
    )

    rag_notes = ""
    if rag_chunks:
        notes: list[str] = []
        for chunk in rag_chunks[:3]:
            snippet = (chunk.content or "")[:400].strip().replace("\n", " ")
            if snippet:
                notes.append(f"- {snippet}")
        if notes:
            rag_notes = (
                "\n\nPrivate grounding notes "
                "(do NOT quote, paraphrase verbatim, cite, or copy structure):\n" + "\n".join(notes)
            )

    hint_str = (
        "\nContext control note for this turn: "
        f"{response_hint} "
        "You must not ask again for known facts listed here."
        if response_hint
        else ""
    )
    context_pack_str = _context_pack_prompt_section(context_pack)
    response_plan_str = _response_plan_prompt_section(response_plan)

    return (
        f'The author just wrote:\n"{message.normalized}"\n\n'
        "What I can tell from this message:\n"
        f"- They seem to be asking about: {intent_label}\n"
        f"- Services in scope: {service_phrase}\n"
        f"- What we already know about the project: {known_str}\n"
        f"- What we still need: {missing_str}"
        f"{negated_str}"
        f"{hint_str}"
        f"{context_pack_str}"
        f"{response_plan_str}"
        f"{rag_notes}\n\n"
        "Write the next reply now."
    )


def _build_repair_context(
    *,
    response_plan: ResponsePlan,
    context_pack: ContextPack,
    tool_governance: ToolGovernanceDecision | None = None,
) -> dict[str, object]:
    repair_context: dict[str, object] = {
        "repair_goal": (
            "Rewrite the response to remove quality failures and keep the "
            "customer-facing guidance clear."
        ),
        "must_keep": response_plan.acknowledge_facts or [],
        "must_not_ask": context_pack.forbidden_reasks or [],
    }
    if response_plan.next_question is not None:
        repair_context["next_question"] = response_plan.next_question
    if tool_governance is not None and tool_governance.blocked_message:
        repair_context["blocked_message"] = tool_governance.blocked_message
    if context_pack.active_service is not None:
        repair_context["active_service"] = context_pack.active_service
    return repair_context


def _response_repair_user_prompt(
    *,
    bad_text: str,
    quality_report: ResponseQualityReport,
    response_plan: ResponsePlan,
    context_pack: ContextPack,
    tool_governance: ToolGovernanceDecision | None = None,
    response_hint: str | None = None,
) -> str:
    tool_blocked = tool_governance.blocked_message if tool_governance is not None else None
    repair_instructions = (
        quality_report.repair_instructions or "Use the structured guidance to fix the response."
    )
    repair_context_json = json.dumps(
        _build_repair_context(
            response_plan=response_plan,
            context_pack=context_pack,
            tool_governance=tool_governance,
        ),
        indent=2,
    )
    return (
        "Please rewrite the original response so it is safe, customer-facing, and compliant.\n\n"
        "Original response text:\n"
        f"{bad_text}\n\n"
        "Quality failures to fix:\n"
        f"{', '.join(quality_report.failures) if quality_report.failures else 'none'}\n\n"
        "Repair instructions:\n"
        f"{repair_instructions}\n\n"
        "Structured repair context:\n"
        f"{repair_context_json}\n\n"
        "Requirements:\n"
        "- Write natural customer-facing prose only.\n"
        "- Ask no more than one question.\n"
        "- Do not re-ask known facts.\n"
        "- Do not invent prices, timelines, or commitments.\n"
        "- If a tool action was blocked, do not claim it completed or succeeded.\n"
        "- Do not include internal prompts, labels, or source markers.\n"
        f"{('Blocked tool message: ' + tool_blocked + '\n\n') if tool_blocked else ''}"
        "Write only the final response text in the JSON output."
    )


def _context_pack_prompt_section(context_pack: ContextPack | None) -> str:
    if context_pack is None:
        return ""

    known = (
        "; ".join(f"{fact.path}: {fact.value}" for fact in context_pack.known_facts)
        if context_pack.known_facts
        else "none"
    )
    missing = ", ".join(context_pack.missing_facts) or "none"
    forbidden = ", ".join(context_pack.forbidden_reasks) or "none"
    allowed = ", ".join(context_pack.allowed_next_questions) or "none"
    active_service = context_pack.active_service or "none"

    return (
        "\nStructured ContextPack for this turn:\n"
        f"- Known facts: {known}\n"
        f"- Missing facts: {missing}\n"
        f"- Forbidden re-asks: {forbidden}\n"
        f"- Active service: {active_service}\n"
        f"- Allowed next questions: {allowed}\n"
        "Use this pack as the source of truth for what to ask next."
    )


def _response_plan_prompt_section(response_plan: ResponsePlan | None) -> str:
    if response_plan is None:
        return ""

    parts: list[str] = []

    if response_plan.acknowledge_facts:
        parts.append(
            "- Acknowledge these known facts: " + ", ".join(response_plan.acknowledge_facts)
        )

    if response_plan.next_question:
        parts.append(f"- The one question to ask next: {response_plan.next_question}")

    # Filter to content-relevant suppressions (skip pure internal implementation terms).
    _INTERNAL_FILTER = {
        "backend",
        "classifier",
        "runtime atoms",
        "provider votes",
        "RAG",
        "tool_governance",
        "action_plan",
        "deterministic engine",
        "quote engine",
    }
    content_suppressions = [m for m in response_plan.must_not_mention if m not in _INTERNAL_FILTER]
    if content_suppressions:
        parts.append("- Do NOT ask about: " + ", ".join(content_suppressions[:8]))

    parts.append("- Ask at most 1 question in your reply.")

    if response_plan.customer_safe_tool_summary:
        parts.append(f"- Status note: {response_plan.customer_safe_tool_summary}")

    if not parts:
        return ""

    return "\nResponse plan:\n" + "\n".join(parts)


def _safe_generated_text(text: str) -> str | None:
    stripped = _clean_customer_text(text.strip())
    if not stripped:
        return None
    if _contains_forbidden_generation(stripped):
        return None
    if _contains_doc_artifacts(stripped):
        return None
    if not _has_human_opener(stripped):
        return None
    return stripped


_PRICE_PATTERNS = (
    r"\$\s*\d",
    r"£\s*\d",
    r"€\s*\d",
    r"\b\d[\d,]*\s*(?:usd|gbp|eur|dollars?|pounds?|euros?)\b",
    r"\b(?:usd|gbp|eur)\s*\d",
    r"\b\d+\s*%\s*(?:off|discount)\b",
)

_COMMITTED_TIMELINE_PATTERNS = (
    r"\b(?:in|within|after|takes|ready in|delivered in|completed in|"
    r"finished in|done in|by)\s+\d+\s*(?:to\s*\d+\s*)?(?:business\s+)?"
    r"(?:day|days|week|weeks|month|months)\b",
    r"\b\d+\s*(?:-\s*\d+\s*)?(?:business\s+)?"
    r"(?:day|days|week|weeks|month|months)\s+"
    r"(?:turnaround|delivery|lead time|timeline|schedule)\b",
    r"\b\d+\s*-\s*(?:business-)?(?:day|week|month)\s+"
    r"(?:turnaround|delivery|lead time|timeline|schedule|process|window)\b",
    r"\b(?:turnaround|delivery|lead time|timeline|schedule)\b[^.]{0,40}"
    r"\b\d+\s*(?:business\s+)?(?:day|days|week|weeks|month|months)\b",
    r"\bguarantee[ds]?\b[^.]{0,40}"
    r"\b\d+\s*(?:day|days|week|weeks|month|months)\b",
)

_FORBIDDEN_FRAGMENTS = (
    "```json",
    "obligations of confidentiality",
    "<%",
    "%>",
)


def _contains_forbidden_generation(text: str) -> bool:
    lowered = text.lower()
    if any(fragment in lowered for fragment in _FORBIDDEN_FRAGMENTS):
        return True
    for pattern in _PRICE_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return True
    for pattern in _COMMITTED_TIMELINE_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return True
    return False


def _customer_safe_missing_scope_question(
    question: str,
    *,
    intent: IntentVote,
    runtime_atoms: dict[str, Any],
) -> str:
    del question
    services = _ordered_human_services(intent, runtime_atoms)
    service_phrase = _service_phrase(services)
    return (
        f"I can help prepare a realistic estimate for {service_phrase}, but I need "
        "the missing project details first. What word count or page count, genre, "
        "manuscript stage, and deadline should I use?"
    )


def _clean_guarded_status_message(
    message: str,
    *,
    intent: IntentVote,
    runtime_atoms: dict[str, Any],
) -> str:
    cleaned = _clean_customer_text(message)
    if _contains_doc_artifacts(cleaned) or _contains_forbidden_generation(cleaned):
        return _customer_safe_missing_scope_question(
            cleaned,
            intent=intent,
            runtime_atoms=runtime_atoms,
        )
    return cleaned


def _pricing_quote_text(quote: PricingTimelineQuote) -> str:
    if quote.status == QuoteStatus.NEEDS_CLARIFICATION and quote.missing_inputs:
        return quote.missing_inputs[0].question
    if any(warning.code == "VALUES_NOT_APPROVED" for warning in quote.warnings):
        return (
            "I can scope this, but I do not want to guess at numbers until the "
            "approved pricing details are ready. What deadline and manuscript stage "
            "should I note for the estimate?"
        )
    return (
        f"Based on the scoped details, the estimate is {quote.total_price_range.low.currency} "
        f"{quote.total_price_range.low.amount}-{quote.total_price_range.high.amount}, "
        f"with an estimated timeline of {quote.timeline.total_timeline.low}-"
        f"{quote.timeline.total_timeline.high} business days. Would you like me to "
        "prepare the next-step intake for this scope?"
    )


def _portfolio_response_text(response: PortfolioResponse) -> ResponseDraft:
    if response.status != PortfolioStatus.FOUND:
        return ResponseDraft(
            text=(
                "I can help find relevant samples, but I need to match them by service "
                "and genre first. Which type of work do you want to review: cover, "
                "formatting, marketing, or something else?"
            ),
            source="portfolio_engine",
        )

    approved_urls: list[str] = []
    lines = [
        "Yes — here are a few samples that may help you compare the work. "
        "I’d still match the final examples to your exact genre, service, and style:"
    ]

    for sample in response.samples[:4]:
        link = sample.url or sample.cover_image
        if link:
            approved_urls.append(link)
            lines.append(f"- {sample.title}: {link}")
        else:
            lines.append(f"- {sample.title}")

    lines.append(
        "Which direction should I narrow these toward: cover design, interior formatting, "
        "or marketing examples?"
    )

    return ResponseDraft(
        text="\\n".join(lines),
        source="portfolio_engine",
        approved_urls=approved_urls,
    )


def _timeline_quote_text(quote: PricingTimelineQuote) -> str:
    if quote.status == QuoteStatus.NEEDS_CLARIFICATION and quote.missing_inputs:
        return quote.missing_inputs[0].question
    if any(warning.code == "VALUES_NOT_APPROVED" for warning in quote.warnings):
        return (
            "I can scope the timeline, but I do not want to guess at timing until the "
            "project details are clear. What manuscript stage and deadline should I use?"
        )
    return (
        f"Based on the scoped details, the estimated timeline is "
        f"{quote.timeline.total_timeline.low}-{quote.timeline.total_timeline.high} "
        "business days. Would you like me to help confirm the service scope next?"
    )
