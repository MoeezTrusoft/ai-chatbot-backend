from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import structlog
from prometheus_client import Counter, Histogram

from bookcraft.components.extraction import CombinedExtractor, StateApplier
from bookcraft.components.extraction.schemas import CombinedExtraction, StateDelta
from bookcraft.components.intent import EnsembleIntentClassifier
from bookcraft.components.intent.hardening import harden_intent_from_message
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
    trimatch_shadow_engine: TriMatchEngine | None = None
    trimatch_extra_mode: str = "off"
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
            trimatch_shadow_result = None
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

            if self.trimatch_shadow_engine is not None:
                try:
                    trimatch_shadow_result = self.trimatch_shadow_engine.classify(processed)
                    if self.trimatch_extra_mode == "shadow":
                        (
                            event_id,
                            event_sequence,
                            previous_event_hash,
                        ) = await self._append_thread_event(
                            thread_id=thread_id,
                            sequence=event_sequence,
                            previous_hash=previous_event_hash,
                            event_type="trimatch.extra_shadow_voted",
                            payload=trimatch_shadow_result.model_dump(mode="json"),
                        )
                        event_ids.append(event_id)
                except Exception as exc:
                    failure_event_type = (
                        "trimatch.extra_advisory_failed"
                        if self.trimatch_extra_mode == "advisory"
                        else "trimatch.extra_shadow_failed"
                    )
                    structlog.get_logger(__name__).warning(
                        failure_event_type,
                        thread_id=str(thread_id),
                        exception_class=exc.__class__.__name__,
                    )
                    event_id, event_sequence, previous_event_hash = await self._append_thread_event(
                        thread_id=thread_id,
                        sequence=event_sequence,
                        previous_hash=previous_event_hash,
                        event_type=failure_event_type,
                        payload={"exception_class": exc.__class__.__name__},
                    )
                    event_ids.append(event_id)

            intent = await self.intent_classifier.classify(
                processed,
                state,
                trimatch_result=trimatch_result,
            )
            ensemble_intent = intent
            intent = harden_intent_from_message(intent, processed)
            decision = self.intent_classifier.last_decision

            if self.trimatch_extra_mode == "advisory" and trimatch_shadow_result is not None:
                event_id, event_sequence, previous_event_hash = await self._append_thread_event(
                    thread_id=thread_id,
                    sequence=event_sequence,
                    previous_hash=previous_event_hash,
                    event_type="trimatch.extra_advisory_recommended",
                    payload=_trimatch_advisory_payload(
                        extra_advisory=trimatch_shadow_result,
                        final_intent=intent,
                    ),
                )
                event_ids.append(event_id)

            extra_shadow_for_disagreement = (
                trimatch_shadow_result if self.trimatch_extra_mode == "shadow" else None
            )

            disagreement_payload = _trimatch_disagreement_payload(
                active_trimatch=trimatch_result,
                extra_shadow=extra_shadow_for_disagreement,
                ensemble_intent=ensemble_intent,
                final_intent=intent,
            )
            if disagreement_payload["should_log"]:
                event_id, event_sequence, previous_event_hash = await self._append_thread_event(
                    thread_id=thread_id,
                    sequence=event_sequence,
                    previous_hash=previous_event_hash,
                    event_type="trimatch.disagreement_observed",
                    payload=disagreement_payload,
                )
                event_ids.append(event_id)

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
                try:
                    rag_chunks = await self.rag_retriever.retrieve(processed, intent)
                except Exception as exc:
                    structlog.get_logger(__name__).warning(
                        "rag_retrieval_failed",
                        thread_id=str(thread_id),
                        query_intent=intent.query_primary.value,
                        service_intent=intent.service_primary.value
                        if intent.service_primary
                        else None,
                        exception_class=exc.__class__.__name__,
                    )
                    event_id, event_sequence, previous_event_hash = await self._append_thread_event(
                        thread_id=thread_id,
                        sequence=event_sequence,
                        previous_hash=previous_event_hash,
                        event_type="rag.failed",
                        payload={
                            "query_intent": intent.query_primary.value,
                            "service_intent": intent.service_primary.value
                            if intent.service_primary
                            else None,
                            "exception_class": exc.__class__.__name__,
                        },
                    )
                    event_ids.append(event_id)
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
                try:
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
                except Exception as exc:
                    structlog.get_logger(__name__).warning(
                        "trg_update_failed",
                        thread_id=str(thread_id),
                        exception_class=exc.__class__.__name__,
                    )
                    event_id, event_sequence, previous_event_hash = await self._append_thread_event(
                        thread_id=thread_id,
                        sequence=event_sequence,
                        previous_hash=previous_event_hash,
                        event_type="trg.failed",
                        payload={"exception_class": exc.__class__.__name__},
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
            return (
                None,
                None,
                "Which BookCraft service and scope should I price through the "
                "deterministic quote engine? I cannot promise discounts or guarantees, "
                "and customer-facing pricing values must be approved before numbers "
                "are shown.",
            )
        word_count = state.project.word_count.value
        page_count = state.project.page_count.value
        genre = state.project.genre.value or _genre_from_text(message)
        if word_count is None and page_count is None:
            return (
                None,
                None,
                "To use the deterministic quote engine, approximately how many words "
                "or pages is your manuscript? BookCraft cannot show pricing, discounts, "
                "payment plans, or timing until the approved engine has enough scope.",
            )
        confirmation_question = _pricing_confirmation_question(
            service=str(service),
            state=state,
            message=message,
            word_count=word_count,
            page_count=page_count,
            genre=genre,
        )
        if confirmation_question is not None:
            return None, None, confirmation_question

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
                "field_meta_snapshot": _pricing_field_meta_snapshot(
                    service=str(service),
                    state=state,
                    message=message,
                    word_count=word_count,
                    page_count=page_count,
                    genre=genre,
                ),
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


def _trimatch_advisory_payload(
    *,
    extra_advisory: object,
    final_intent: IntentVote,
) -> dict[str, Any]:
    extra_snapshot = _trimatch_snapshot(extra_advisory)
    final_snapshot = _intent_snapshot(final_intent)

    return {
        "extra_advisory": extra_snapshot,
        "final": final_snapshot,
        "recommendation": _advisory_recommendation(
            extra_snapshot=extra_snapshot,
            final_snapshot=final_snapshot,
        ),
        "advisory_applied": False,
        "side_effects_allowed": False,
    }


def _advisory_recommendation(
    *,
    extra_snapshot: dict[str, Any] | None,
    final_snapshot: dict[str, Any],
) -> dict[str, Any]:
    if extra_snapshot is None:
        return {
            "dimension": None,
            "recommended_value": None,
            "matches_final": False,
            "reason": "extra advisory RulePack produced no usable snapshot",
        }

    for dimension in ("query_primary", "service_primary", "funnel_stage"):
        recommended_value = extra_snapshot.get(dimension)
        if recommended_value is None:
            continue

        matches_final = recommended_value == final_snapshot.get(dimension)
        return {
            "dimension": dimension,
            "recommended_value": recommended_value,
            "matches_final": matches_final,
            "reason": (
                "extra advisory RulePack agreed with final intent"
                if matches_final
                else "extra advisory RulePack differed from final intent"
            ),
        }

    return {
        "dimension": None,
        "recommended_value": None,
        "matches_final": False,
        "reason": "extra advisory RulePack produced no recommendation",
    }


def _trimatch_disagreement_payload(
    *,
    active_trimatch: object | None,
    extra_shadow: object | None,
    ensemble_intent: IntentVote,
    final_intent: IntentVote,
) -> dict[str, Any]:
    final_snapshot = _intent_snapshot(final_intent)
    snapshots: dict[str, dict[str, Any] | None] = {
        "active_trimatch": _trimatch_snapshot(active_trimatch),
        "extra_shadow": _trimatch_snapshot(extra_shadow),
        "ensemble": _intent_snapshot(ensemble_intent),
    }

    disagreements: list[dict[str, Any]] = []
    for source, snapshot in snapshots.items():
        if snapshot is None:
            continue
        for dimension in ("query_primary", "service_primary", "funnel_stage"):
            source_value = snapshot.get(dimension)
            final_value = final_snapshot.get(dimension)
            if source_value is None:
                continue
            if source_value != final_value:
                disagreements.append(
                    {
                        "source": source,
                        "dimension": dimension,
                        "source_value": source_value,
                        "final_value": final_value,
                    }
                )

    extra_shadow_snapshot = snapshots["extra_shadow"]
    should_log = bool(disagreements)
    if (
        extra_shadow_snapshot is not None
        and int(extra_shadow_snapshot.get("evidence_count") or 0) > 0
    ):
        should_log = True

    return {
        "active_trimatch": snapshots["active_trimatch"],
        "extra_shadow": extra_shadow_snapshot,
        "ensemble": snapshots["ensemble"],
        "final": final_snapshot,
        "disagreements": disagreements,
        "should_log": should_log,
    }


def _trimatch_snapshot(result: object | None) -> dict[str, Any] | None:
    if result is None:
        return None

    return {
        "query_primary": _enum_value(getattr(result, "query_primary", None)),
        "service_primary": _enum_value(getattr(result, "service_primary", None)),
        "funnel_stage": _enum_value(getattr(result, "funnel_stage", None)),
        "confidence": getattr(result, "confidence", None),
        "evidence_count": len(getattr(result, "evidence", []) or []),
        "shortcut_eligible": bool(getattr(result, "shortcut_eligible", False)),
    }


def _intent_snapshot(intent: IntentVote) -> dict[str, Any]:
    return {
        "query_primary": _enum_value(intent.query_primary),
        "service_primary": _enum_value(intent.service_primary),
        "funnel_stage": _enum_value(intent.funnel_stage),
        "confidence": intent.confidence,
        "evidence_count": len(intent.evidence),
        "needs_clarification": intent.needs_clarification,
    }


def _enum_value(value: object | None) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", None)
    return str(enum_value if enum_value is not None else value)


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


def _pricing_confirmation_question(
    *,
    service: str,
    state: ThreadState,
    message: str,
    word_count: int | None,
    page_count: int | None,
    genre: str | None,
) -> str | None:
    assumptions = _unsafe_pricing_defaults(
        service=service,
        state=state,
        message=message,
        word_count=word_count,
        page_count=page_count,
        genre=genre,
    )
    if not assumptions:
        return None

    assumption_list = "; ".join(assumptions)
    return (
        "I can run the deterministic quote engine after you confirm these scoping "
        f"details first: {assumption_list}. BookCraft cannot show approved pricing, "
        "discounts, payment plans, or timelines until the scope is confirmed. "
        "Please confirm or correct them so I do not price the project using hidden "
        "assumptions."
    )


def _unsafe_pricing_defaults(
    *,
    service: str,
    state: ThreadState,
    message: str,
    word_count: int | None,
    page_count: int | None,
    genre: str | None,
) -> list[str]:
    lowered = message.casefold()
    assumptions: list[str] = []

    if genre is None:
        assumptions.append("genre/category")

    if service == "ghostwriting":
        if not _mentions_any(lowered, ["full ghostwriting", "full book", "from scratch"]):
            assumptions.append("ghostwriting scope = full ghostwriting")
        if not state.project.manuscript_status.value and not _mentions_any(
            lowered,
            ["outline", "outline ready", "draft", "idea", "manuscript ready", "not written"],
        ):
            assumptions.append("manuscript status = outline ready")

    elif service == "editing_proofreading":
        if not _mentions_any(
            lowered,
            ["proofreading", "copy editing", "copyediting", "line editing", "developmental"],
        ):
            assumptions.append("editing type = copy editing")
        if not _mentions_any(lowered, ["clean draft", "rough draft", "average", "heavy edit"]):
            assumptions.append("manuscript condition = average")

    elif service == "interior_formatting":
        if page_count is None:
            assumptions.append("page count inferred from word count")
        if not _mentions_any(lowered, ["print", "ebook", "e-book", "kindle", "kdp"]):
            assumptions.append("format target = print + ebook")

    elif service == "cover_design_illustration":
        if not _mentions_any(lowered, ["ebook", "e-book", "print", "paperback", "hardcover"]):
            assumptions.append("cover format = ebook + print")
        if not _mentions_any(lowered, ["front cover", "full cover", "back cover", "spine"]):
            assumptions.append("cover scope = front cover")
        if not _mentions_any(lowered, ["simple", "standard", "complex", "illustrated", "premium"]):
            assumptions.append("cover complexity = standard")

    elif service == "audiobook_production":
        if word_count is None:
            assumptions.append("audiobook length inferred from manuscript size")
        if not _mentions_any(lowered, ["single narrator", "dual narrator", "voice cast"]):
            assumptions.append("narration model = single narrator")

    elif service == "publishing_distribution":
        if not _mentions_any(lowered, ["ebook", "e-book", "print", "paperback", "hardcover"]):
            assumptions.append("publishing package = ebook + print")
        if not _mentions_any(lowered, ["basic", "professional", "premium"]):
            assumptions.append("publishing tier = professional")

    elif service == "marketing_promotion":
        if not _mentions_any(lowered, ["launch", "reviews", "visibility", "ads", "social"]):
            assumptions.append("campaign goal = launch support")
        if not _mentions_any(lowered, ["1 month", "2 months", "3 months", "90 days"]):
            assumptions.append("campaign duration = standard campaign duration")

    elif service == "author_website":
        if not _mentions_any(lowered, ["landing page", "book launch", "author site", "website"]):
            assumptions.append("website type = book launch")
        if not _mentions_any(lowered, ["basic", "professional", "premium"]):
            assumptions.append("website tier = professional")

    elif service == "video_trailer":
        if not _mentions_any(lowered, ["30 second", "30-second", "60 second", "60-second"]):
            assumptions.append("video length = 60 seconds")
        if not _mentions_any(lowered, ["simple motion", "cinematic", "animated", "live action"]):
            assumptions.append("production style = simple motion")

    return assumptions


def _pricing_field_meta_snapshot(
    *,
    service: str,
    state: ThreadState,
    message: str,
    word_count: int | None,
    page_count: int | None,
    genre: str | None,
) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "service": _pricing_field_meta(
            value=service,
            confidence=0.9,
            source="ai_extracted",
            raw_excerpt=message[:240],
        ),
        "pricing_safety": _pricing_field_meta(
            value={
                "hidden_defaults_allowed": False,
                "raw_message_char_count": len(message),
            },
            confidence=1.0,
            source="system",
            raw_excerpt=None,
        ),
    }
    if word_count is not None:
        snapshot["word_count"] = _pricing_field_meta(
            value=word_count,
            confidence=state.project.word_count.confidence or 0.9,
            source="user_stated",
            raw_excerpt=state.project.word_count.raw_excerpt,
        )
    if page_count is not None:
        snapshot["page_count"] = _pricing_field_meta(
            value=page_count,
            confidence=state.project.page_count.confidence or 0.9,
            source="user_stated",
            raw_excerpt=state.project.page_count.raw_excerpt,
        )
    if genre is not None:
        snapshot["genre"] = _pricing_field_meta(
            value=genre,
            confidence=state.project.genre.confidence or 0.85,
            source="ai_extracted",
            raw_excerpt=message[:240],
        )
    if state.project.manuscript_status.value:
        snapshot["manuscript_status"] = _pricing_field_meta(
            value=state.project.manuscript_status.value,
            confidence=state.project.manuscript_status.confidence or 0.85,
            source="user_stated",
            raw_excerpt=state.project.manuscript_status.raw_excerpt,
        )
    return snapshot


def _pricing_field_meta(
    *,
    value: object,
    confidence: float,
    source: str,
    raw_excerpt: str | None,
) -> dict[str, object]:
    return {
        "value": value,
        "confidence": confidence,
        "source": source,
        "extracted_by": "chat_service.pricing_assumption_safety",
        "raw_excerpt": raw_excerpt,
    }


def _mentions_any(text: str, fragments: list[str]) -> bool:
    return any(fragment in text for fragment in fragments)


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
