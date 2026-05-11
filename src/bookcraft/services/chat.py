from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from prometheus_client import Counter, Histogram

from bookcraft.components.extraction import CombinedExtractor, StateApplier
from bookcraft.components.extraction.schemas import CombinedExtraction, StateDelta
from bookcraft.components.intent import EnsembleIntentClassifier
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.language_guard import LanguageGuard
from bookcraft.components.portfolio import PortfolioEngine, PortfolioRequest, PortfolioResponse
from bookcraft.components.preprocessor import SharedPreprocessor
from bookcraft.components.pricing import (
    PricingQuoteRequest,
    PricingTimelineEngine,
    PricingTimelineQuote,
)
from bookcraft.components.rag.retriever import RagRetriever
from bookcraft.components.response import ResponseFormatter, SonnetResponseGenerator
from bookcraft.components.storage.events import calculate_event_hash
from bookcraft.components.storage.thread_repository import (
    LoadedThread,
    ThreadRepository,
)
from bookcraft.components.trg import TemporalRelationGraphEngine
from bookcraft.components.trimatch import TriMatchEngine
from bookcraft.domain.enums import QueryIntentType, ServiceCategory, Source
from bookcraft.domain.state import ThreadState
from bookcraft.infra.redaction import redact_mapping
from bookcraft.tools import ToolContext, ToolDispatcher

if TYPE_CHECKING:
    from bookcraft.api.chat import ChatTurnRequest, ChatTurnResponse

CHAT_TURNS_TOTAL = Counter("chatbot_turns_total", "Chat turns handled.")
CHAT_TURN_LATENCY = Histogram("chatbot_turn_latency_seconds", "Chat turn latency.")
STATE_UPDATES = Counter("thread_state_updates_total", "Thread state updates.", ["result"])


@dataclass(slots=True)
class ThreadMemory:
    state: ThreadState = field(default_factory=ThreadState)
    events: list[dict[str, object]] = field(default_factory=list)


@dataclass(slots=True)
class ChatService:
    language_guard: LanguageGuard
    preprocessor: SharedPreprocessor
    intent_classifier: EnsembleIntentClassifier
    extractor: CombinedExtractor
    state_applier: StateApplier
    response_generator: SonnetResponseGenerator
    formatter: ResponseFormatter
    rag_retriever: RagRetriever | None = None
    pricing_engine: PricingTimelineEngine | None = None
    portfolio_engine: PortfolioEngine | None = None
    tool_dispatcher: ToolDispatcher | None = None
    trg_engine: TemporalRelationGraphEngine | None = None
    trimatch_engine: TriMatchEngine | None = None
    threads: dict[UUID, ThreadMemory] = field(default_factory=dict)
    thread_repository: ThreadRepository | None = None
    environment: str = "dev"

    async def handle_turn(self, payload: ChatTurnRequest) -> ChatTurnResponse:
        from bookcraft.api.chat import ChatTurnResponse

        CHAT_TURNS_TOTAL.inc()
        with CHAT_TURN_LATENCY.time():
            thread = await self._load_thread(payload)
            thread_id = thread.thread_id
            state = thread.state
            event_sequence = thread.event_count
            previous_event_hash = thread.last_event_hash
            language = self.language_guard.detect(payload.message, cached_language="en")
            event_id, event_sequence, previous_event_hash = await self._append_thread_event(
                thread_id=thread_id,
                sequence=event_sequence,
                previous_hash=previous_event_hash,
                event_type="user.message",
                payload={"text": payload.message},
            )
            event_ids = [event_id]
            if not language.is_english:
                bubbles = self.formatter.format(language.redirect_message or "")
                event_id, event_sequence, previous_event_hash = await self._append_thread_event(
                    thread_id=thread_id,
                    sequence=event_sequence,
                    previous_hash=previous_event_hash,
                    event_type="assistant.redirect",
                    payload={"language": language.language},
                )
                event_ids.append(event_id)
                return ChatTurnResponse(
                    thread_id=thread_id,
                    bubbles=bubbles,
                    intent=None,
                    language_status=language.language,
                    debug_event_ids=self._debug_event_ids(event_ids),
                )

            processed = await self.preprocessor.process(payload.message, language=language.language)
            trimatch_result = None
            if self.trimatch_engine is not None:
                trimatch_result = self.trimatch_engine.classify(processed)
                event_id, event_sequence, previous_event_hash = await self._append_thread_event(
                    thread_id=thread_id,
                    sequence=event_sequence,
                    previous_hash=previous_event_hash,
                    event_type="trimatch.voted",
                    payload=trimatch_result.model_dump(mode="json"),
                )
                event_ids.append(event_id)
            intent = await self.intent_classifier.classify(
                processed,
                state,
                trimatch_result=trimatch_result,
            )
            decision = self.intent_classifier.last_decision
            event_id, event_sequence, previous_event_hash = await self._append_thread_event(
                thread_id=thread_id,
                sequence=event_sequence,
                previous_hash=previous_event_hash,
                event_type="intent.classified",
                payload={
                    "intent": intent.model_dump(mode="json"),
                    "decision": decision.model_dump(mode="json") if decision else None,
                },
            )
            event_ids.append(event_id)
            extraction = await self.extractor.extract(processed, state)
            previous_state = state.model_copy(deep=True)
            state = self.state_applier.apply(state, extraction)
            STATE_UPDATES.labels(result="applied").inc()
            event_id, event_sequence, previous_event_hash = await self._append_thread_event(
                thread_id=thread_id,
                sequence=event_sequence,
                previous_hash=previous_event_hash,
                event_type="extraction.applied",
                payload={"delta_count": len(extraction.state_deltas)},
            )
            event_ids.append(event_id)
            rag_chunks = []
            if self.rag_retriever is not None and _allow_rag_for_intent(intent):
                rag_chunks = await self.rag_retriever.retrieve(processed, intent)
            pricing_quote: PricingTimelineQuote | None = None
            timeline_estimate: PricingTimelineQuote | None = None
            pricing_missing_question: str | None = None
            if intent.query_primary in {
                QueryIntentType.PRICING_QUESTION,
                QueryIntentType.TIMELINE_QUESTION,
            }:
                pricing_quote, timeline_estimate, pricing_missing_question = await self._price_turn(
                    thread_id=thread_id,
                    customer_id=payload.customer_id,
                    turn_sequence=event_sequence + 1,
                    correlation_id=payload.correlation_id,
                    state=state,
                    intent_service=intent.service_primary,
                    message=payload.message,
                    confidence=intent.confidence,
                )
                if pricing_quote is not None:
                    state = self.state_applier.apply(
                        state,
                        CombinedExtraction(
                            state_deltas=[
                                StateDelta(
                                    path="commercial.latest_quote_id",
                                    value=str(pricing_quote.quote_id),
                                    confidence=1.0,
                                    source=Source.SYSTEM,
                                    extracted_by="pricing_engine.v1",
                                    raw_excerpt=None,
                                )
                            ]
                        ),
                    )
            portfolio_response: PortfolioResponse | None = None
            if intent.query_primary == QueryIntentType.PORTFOLIO_REQUEST:
                portfolio_response = await self._portfolio_turn(
                    thread_id=thread_id,
                    customer_id=payload.customer_id,
                    turn_sequence=event_sequence + 1,
                    correlation_id=payload.correlation_id,
                    state=state,
                    intent_service=intent.service_primary,
                    message=payload.message,
                )
            document_status_message = _document_status_message(intent)
            draft = await self.response_generator.generate(
                message=processed,
                state=state,
                intent=intent,
                extraction=extraction,
                rag_chunks=rag_chunks,
                pricing_quote=pricing_quote,
                timeline_estimate=timeline_estimate,
                pricing_missing_question=pricing_missing_question,
                portfolio_response=portfolio_response,
                document_status_message=document_status_message,
            )
            bubbles = self.formatter.format(draft.text, approved_urls=set(draft.approved_urls))
            if self.trg_engine is not None:
                trg_result = await self.trg_engine.update_after_turn(
                    thread_id=thread_id,
                    turn_sequence=event_sequence + 1,
                    user_text=payload.message,
                    assistant_text=draft.text,
                    previous_state=previous_state,
                    state_deltas=extraction.state_deltas,
                )
                event_id, event_sequence, previous_event_hash = await self._append_thread_event(
                    thread_id=thread_id,
                    sequence=event_sequence,
                    previous_hash=previous_event_hash,
                    event_type="trg.updated",
                    payload={
                        "node_count": len(trg_result.graph.nodes),
                        "edge_count": len(trg_result.graph.edges),
                        "unresolved_question_count": trg_result.unresolved_question_count,
                        "contradiction_count": trg_result.contradiction_count,
                    },
                )
                event_ids.append(event_id)
            event_id, event_sequence, previous_event_hash = await self._append_thread_event(
                thread_id=thread_id,
                sequence=event_sequence,
                previous_hash=previous_event_hash,
                event_type="assistant.response",
                payload={
                    "intent": intent.model_dump(mode="json"),
                    "bubble_count": len(bubbles),
                    "source": draft.source,
                },
            )
            event_ids.append(event_id)
            if self.thread_repository is not None:
                await self.thread_repository.save_state(
                    thread_id=thread_id,
                    state=state,
                    expected_version=thread.version,
                    language=language.language,
                )
            else:
                self.threads[thread_id].state = state
            return ChatTurnResponse(
                thread_id=thread_id,
                bubbles=bubbles,
                intent=intent,
                language_status=language.language,
                debug_event_ids=self._debug_event_ids(event_ids),
            )

    @staticmethod
    def _append_event(
        memory: ThreadMemory,
        thread_id: UUID,
        event_type: str,
        payload: dict[str, object],
    ) -> str:
        safe_payload = redact_mapping(payload) or {}
        sequence = len(memory.events) + 1
        previous_hash = str(memory.events[-1]["event_hash"]) if memory.events else None
        event_hash = calculate_event_hash(
            thread_id=thread_id,
            sequence=sequence,
            event_type=event_type,
            payload=safe_payload,
            previous_hash=previous_hash,
        )
        memory.events.append(
            {
                "sequence": sequence,
                "event_type": event_type,
                "payload": safe_payload,
                "previous_hash": previous_hash,
                "event_hash": event_hash,
            }
        )
        return event_hash

    async def _load_thread(self, payload: ChatTurnRequest) -> LoadedThread:
        if self.thread_repository is None:
            thread_id = payload.thread_id or uuid4()
            memory = self.threads.setdefault(thread_id, ThreadMemory())
            return LoadedThread(
                thread_id=thread_id,
                state=memory.state,
                version=0,
                turn_count=len(memory.events),
                event_count=len(memory.events),
                last_event_hash=str(memory.events[-1]["event_hash"]) if memory.events else None,
            )

        return await self.thread_repository.load_or_create(
            thread_id=payload.thread_id,
            customer_id=payload.customer_id,
        )

    def _debug_event_ids(self, event_ids: list[str]) -> list[str]:
        if self.environment in {"test", "dev"}:
            return event_ids
        return []

    async def _append_thread_event(
        self,
        *,
        thread_id: UUID,
        sequence: int,
        previous_hash: str | None,
        event_type: str,
        payload: dict[str, object],
    ) -> tuple[str, int, str]:
        safe_payload = redact_mapping(payload) or {}
        if self.thread_repository is None:
            memory = self.threads.setdefault(thread_id, ThreadMemory())
            event_hash = self._append_event(memory, thread_id, event_type, safe_payload)
            return event_hash, len(memory.events), event_hash

        event_hash = await self.thread_repository.append_event(
            thread_id=thread_id,
            sequence=sequence + 1,
            event_type=event_type,
            payload=safe_payload,
            previous_hash=previous_hash,
        )
        return event_hash, sequence + 1, event_hash

    async def _price_turn(
        self,
        *,
        thread_id: UUID,
        customer_id: UUID | None,
        turn_sequence: int,
        correlation_id: str | None,
        state: ThreadState,
        intent_service: object,
        message: str,
        confidence: float,
    ) -> tuple[PricingTimelineQuote | None, PricingTimelineQuote | None, str | None]:
        del confidence
        if self.pricing_engine is None:
            return None, None, None
        service = intent_service or (
            state.project.services_discussed[0].service.value
            if state.project.services_discussed
            and state.project.services_discussed[0].service.value is not None
            else None
        )
        if service is None:
            return None, None, "Which BookCraft service should I price?"
        word_count = state.project.word_count.value
        page_count = state.project.page_count.value
        genre = state.project.genre.value or _genre_from_text(message)
        if word_count is None and page_count is None:
            return (
                None,
                None,
                "To use the deterministic quote engine, approximately how many words "
                "or pages is your manuscript?",
            )
        request = PricingQuoteRequest.model_validate(
            {
                "thread_id": str(thread_id),
                "requested_services": [str(service)],
                "service_inputs": {
                    str(service): _default_service_inputs(
                        service=str(service),
                        word_count=word_count,
                        page_count=page_count,
                        genre=genre,
                    )
                },
                "global_inputs": {
                    "genre": genre,
                    "word_count": word_count,
                    "page_count": page_count,
                    "manuscript_status": state.project.manuscript_status.value,
                },
            }
        )
        if "timeline" in message.lower() or "how long" in message.lower():
            timeline = await self._invoke_pricing_quote(
                request=request,
                thread_id=thread_id,
                customer_id=customer_id,
                turn_sequence=turn_sequence,
                correlation_id=correlation_id,
            )
            if timeline.missing_inputs:
                return None, None, timeline.missing_inputs[0].question
            return None, timeline, None
        quote = await self._invoke_pricing_quote(
            request=request,
            thread_id=thread_id,
            customer_id=customer_id,
            turn_sequence=turn_sequence,
            correlation_id=correlation_id,
        )
        if quote.missing_inputs:
            return None, None, quote.missing_inputs[0].question
        return quote, None, None

    async def _invoke_pricing_quote(
        self,
        *,
        request: PricingQuoteRequest,
        thread_id: UUID,
        customer_id: UUID | None,
        turn_sequence: int,
        correlation_id: str | None,
    ) -> PricingTimelineQuote:
        if self.tool_dispatcher is None:
            if self.pricing_engine is None:
                msg = "Pricing engine is not configured."
                raise RuntimeError(msg)
            return self.pricing_engine.quote(request)
        raw_input = request.model_dump(mode="json")
        envelope = await self.tool_dispatcher.invoke(
            tool_name="pricing.quote.estimate.v2",
            raw_input=raw_input,
            context=self._tool_context(
                thread_id=thread_id,
                customer_id=customer_id,
                turn_sequence=turn_sequence,
                tool_name="pricing.quote.estimate.v2",
                raw_input=raw_input,
                correlation_id=correlation_id,
            ),
        )
        return PricingTimelineQuote.model_validate(envelope.result)

    async def _portfolio_turn(
        self,
        *,
        thread_id: UUID,
        customer_id: UUID | None,
        turn_sequence: int,
        correlation_id: str | None,
        state: ThreadState,
        intent_service: ServiceCategory | None,
        message: str,
    ) -> PortfolioResponse | None:
        if self.portfolio_engine is None:
            return None
        service = intent_service or (
            state.project.services_discussed[0].service.value
            if state.project.services_discussed
            and state.project.services_discussed[0].service.value is not None
            else None
        )
        if service is None:
            return None
        request = PortfolioRequest(
            service=ServiceCategory(str(service)),
            genre=state.project.genre.value or _genre_from_text(message),
            limit=3,
        )
        if self.tool_dispatcher is None:
            return self.portfolio_engine.request_samples(request)
        raw_input = request.model_dump(mode="json")
        envelope = await self.tool_dispatcher.invoke(
            tool_name="portfolio.request_samples.v1",
            raw_input=raw_input,
            context=self._tool_context(
                thread_id=thread_id,
                customer_id=customer_id,
                turn_sequence=turn_sequence,
                tool_name="portfolio.request_samples.v1",
                raw_input=raw_input,
                correlation_id=correlation_id,
            ),
        )
        return PortfolioResponse.model_validate(envelope.result)

    def _tool_context(
        self,
        *,
        thread_id: UUID,
        customer_id: UUID | None,
        turn_sequence: int,
        tool_name: str,
        raw_input: dict[str, object],
        correlation_id: str | None,
    ) -> ToolContext:
        idempotency_material = {
            "thread_id": str(thread_id),
            "turn_sequence": turn_sequence,
            "tool_name": tool_name,
            "raw_input": raw_input,
        }
        idempotency_key = hashlib.sha256(
            json.dumps(
                idempotency_material,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        return ToolContext(
            thread_id=thread_id,
            customer_id=customer_id,
            turn_sequence=turn_sequence,
            invoked_by="chat_service",
            correlation_id=correlation_id or str(thread_id),
            idempotency_key=idempotency_key,
            environment=self.environment,
        )


def _allow_rag_for_intent(intent: IntentVote) -> bool:
    return intent.query_primary not in {
        QueryIntentType.PRICING_QUESTION,
        QueryIntentType.TIMELINE_QUESTION,
        QueryIntentType.PORTFOLIO_REQUEST,
        QueryIntentType.NDA_REQUEST,
        QueryIntentType.AGREEMENT_REQUEST,
    }


def _document_status_message(intent: IntentVote) -> str | None:
    if intent.query_primary == QueryIntentType.NDA_REQUEST:
        return (
            "I can start the NDA request. NDA text must render from BookCraft's approved "
            "template only, so I need the author name, email, phone, and effective date "
            "before it can enter the document queue."
        )
    if intent.query_primary == QueryIntentType.AGREEMENT_REQUEST:
        return (
            "I can start the service agreement request. Agreement text must render from "
            "BookCraft's approved template only, and fee fields must come from an accepted "
            "deterministic quote before the agreement can enter the document queue."
        )
    return None


def _genre_from_text(text: str) -> str | None:
    lowered = text.lower()
    for genre in [
        "fantasy",
        "romance",
        "thriller",
        "memoir",
        "business",
        "children",
        "nonfiction",
        "non-fiction",
        "fiction",
    ]:
        if genre in lowered:
            return genre
    return None


def _default_service_inputs(
    *,
    service: str,
    word_count: int | None,
    page_count: int | None,
    genre: str | None,
) -> dict[str, object]:
    genre_category = _genre_category(genre)
    if service == "ghostwriting":
        return {
            "service_type": "full_ghostwriting",
            "category": genre_category,
            "word_count": word_count,
            "manuscript_status": "outline_ready",
        }
    if service == "editing_proofreading":
        return {
            "service_type": "copy_editing",
            "category": "standard_fiction",
            "word_count": word_count,
            "manuscript_condition": "average",
        }
    if service == "interior_formatting":
        return {
            "output_format": "print_ebook",
            "category": "fiction",
            "page_count": page_count or max(1, int((word_count or 25000) / 250)),
        }
    if service == "cover_design_illustration":
        return {
            "format": "ebook_print",
            "cover_type": "front_cover",
            "complexity_level": "standard",
        }
    if service == "audiobook_production":
        return {
            "tier": "professional",
            "word_count": word_count,
            "narration_model": "single_narrator",
        }
    if service == "publishing_distribution":
        return {"tier": "professional", "package_dimension": "ebook_print"}
    if service == "marketing_promotion":
        return {
            "tier": "professional_campaign",
            "campaign_duration": "3_months",
            "primary_goal": "launch_support",
        }
    if service == "author_website":
        return {"tier": "professional", "website_type": "book_launch"}
    if service == "video_trailer":
        return {
            "tier": "professional",
            "video_length_seconds": 60,
            "production_style": "simple_motion",
        }
    return {}


def _genre_category(genre: str | None) -> str:
    lowered = (genre or "").lower()
    if "non" in lowered or "business" in lowered or "memoir" in lowered:
        return "nonfiction_standard"
    if "children" in lowered:
        return "childrens_standard"
    if "young" in lowered:
        return "young_adult"
    return "fiction_standard"
