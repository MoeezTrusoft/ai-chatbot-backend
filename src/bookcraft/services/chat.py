from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import structlog
from prometheus_client import Counter, Histogram

from bookcraft.components.actions import (
    ActionPlan,
    ActionResult,
    ActionStatus,
    ActionType,
    SalesActionDispatcher,
    SalesActionPlanner,
    action_trace_payload,
)
from bookcraft.components.analysis import LiveTraceStore
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
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ServiceInterest, ThreadState
from bookcraft.infra.redaction import redact_mapping, redact_text
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
    trace_store: LiveTraceStore | None = None
    action_planner: SalesActionPlanner = field(default_factory=SalesActionPlanner)
    action_dispatcher: SalesActionDispatcher = field(default_factory=SalesActionDispatcher)
    threads: dict[UUID, ThreadMemory] = field(default_factory=dict)
    thread_repository: ThreadRepository | None = None
    environment: str = "dev"

    async def handle_turn(self, payload: ChatTurnRequest) -> ChatTurnResponse:
        from bookcraft.api.chat import ChatTurnResponse

        CHAT_TURNS_TOTAL.inc()
        turn_started = time.perf_counter()
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
                self._record_live_trace(
                    {
                        "thread_id": str(thread_id),
                        "customer_id": str(payload.customer_id)
                        if payload.customer_id is not None
                        else None,
                        "correlation_id": payload.correlation_id,
                        "message_preview": redact_text(payload.message)[:500],
                        "elapsed_ms": round((time.perf_counter() - turn_started) * 1000, 2),
                        "language_status": language.language,
                        "assistant": {
                            "source": "language_guard",
                            "bubble_count": len(bubbles),
                        },
                        "intent": None,
                        "decision": None,
                        "runtime_atoms": {},
                        "event_ids": self._debug_event_ids(event_ids),
                        "recorded_at": datetime.now(UTC).isoformat(),
                    }
                )
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
                    if self.trimatch_extra_mode == "advisory":
                        failure_event_type = "trimatch.extra_advisory_failed"
                    elif self.trimatch_extra_mode == "tiebreaker_candidate":
                        failure_event_type = "trimatch.extra_tiebreaker_failed"
                    elif self.trimatch_extra_mode == "shortcut_candidate":
                        failure_event_type = "trimatch.extra_shortcut_failed"
                    else:
                        failure_event_type = "trimatch.extra_shadow_failed"
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
            intent = self._stabilize_service_context(
                intent=intent,
                processed=processed,
                state=state,
            )
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

            if (
                self.trimatch_extra_mode == "tiebreaker_candidate"
                and trimatch_shadow_result is not None
            ):
                tiebreaker_payload = _trimatch_tiebreaker_considered_payload(
                    active_trimatch=trimatch_result,
                    extra_tiebreaker=trimatch_shadow_result,
                    ensemble_intent=ensemble_intent,
                    final_intent=intent,
                )
                intent = _apply_tiebreaker_to_intent(
                    intent=intent,
                    decision=tiebreaker_payload["decision"],
                )
                tiebreaker_payload["after"] = {
                    "final_after_tiebreaker": _intent_snapshot(intent),
                }
                event_id, event_sequence, previous_event_hash = await self._append_thread_event(
                    thread_id=thread_id,
                    sequence=event_sequence,
                    previous_hash=previous_event_hash,
                    event_type="trimatch.extra_tiebreaker_considered",
                    payload=tiebreaker_payload,
                )
                event_ids.append(event_id)

            if (
                self.trimatch_extra_mode == "shortcut_candidate"
                and trimatch_shadow_result is not None
            ):
                shortcut_payload = _trimatch_shortcut_considered_payload(
                    extra_shortcut=trimatch_shadow_result,
                    final_intent=intent,
                )
                intent = _apply_shortcut_to_intent(
                    intent=intent,
                    shortcut=shortcut_payload["shortcut"],
                )
                shortcut_payload["after"] = {
                    "final_after_shortcut": _intent_snapshot(intent),
                }
                event_id, event_sequence, previous_event_hash = await self._append_thread_event(
                    thread_id=thread_id,
                    sequence=event_sequence,
                    previous_hash=previous_event_hash,
                    event_type="trimatch.extra_shortcut_considered",
                    payload=shortcut_payload,
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
            self._apply_service_focus_to_state(
                state=state,
                processed=processed,
                intent=intent,
            )
            STATE_UPDATES.labels(result="applied").inc()
            event_id, event_sequence, previous_event_hash = await self._append_thread_event(
                thread_id=thread_id,
                sequence=event_sequence,
                previous_hash=previous_event_hash,
                event_type="extraction.applied",
                payload={"delta_count": len(extraction.state_deltas)},
            )
            event_ids.append(event_id)
            action_plan = self.action_planner.plan(
                processed=processed,
                state=state,
                intent=intent,
                extraction=extraction,
            )
            self._apply_sales_action_plan_to_state(state, action_plan)
            action_result = await self.action_dispatcher.dispatch(
                action_plan,
                thread_id=thread_id,
                customer_id=payload.customer_id,
            )
            self._apply_sales_action_result_to_state(state, action_result)
            action_payload = action_trace_payload(action_plan, action_result)
            event_id, event_sequence, previous_event_hash = await self._append_thread_event(
                thread_id=thread_id,
                sequence=event_sequence,
                previous_hash=previous_event_hash,
                event_type=(
                    "sales_action.processed"
                    if action_result is not None
                    else "sales_action.planned"
                ),
                payload=action_payload or {},
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
            portfolio_response: PortfolioResponse | None = self._portfolio_response_from_action(
                action_result
            )
            if (
                portfolio_response is None
                and intent.query_primary == QueryIntentType.PORTFOLIO_REQUEST
            ):
                portfolio_response = await self._portfolio_turn(
                    thread_id=thread_id,
                    customer_id=payload.customer_id,
                    turn_sequence=event_sequence + 1,
                    correlation_id=payload.correlation_id,
                    state=state,
                    intent_service=intent.service_primary,
                    message=payload.message,
                )
            trg_response_hint = await self._build_trg_response_hint(
                thread_id=thread_id,
                state=state,
                intent=intent,
            )
            document_status_message = self._sales_action_status_message(
                action_plan, action_result
            ) or _document_status_message(intent)
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
                runtime_atoms=processed.deterministic_atoms,
                response_hint=trg_response_hint,
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
            self._record_live_trace(
                {
                    "thread_id": str(thread_id),
                    "customer_id": str(payload.customer_id)
                    if payload.customer_id is not None
                    else None,
                    "correlation_id": payload.correlation_id,
                    "message_preview": redact_text(payload.message)[:500],
                    "elapsed_ms": round((time.perf_counter() - turn_started) * 1000, 2),
                    "language_status": language.language,
                    "assistant": {
                        "source": draft.source,
                        "bubble_count": len(bubbles),
                        "preview": redact_text(draft.text)[:500],
                    },
                    "intent": intent.model_dump(mode="json"),
                    "decision": decision.model_dump(mode="json") if decision else None,
                    "trimatch": trimatch_result.model_dump(mode="json")
                    if trimatch_result is not None
                    else None,
                    "trimatch_shadow": trimatch_shadow_result.model_dump(mode="json")
                    if trimatch_shadow_result is not None
                    else None,
                    "runtime_atoms": processed.deterministic_atoms,
                    "action_plan": action_trace_payload(action_plan, action_result),
                    "trg_response_hint": trg_response_hint,
                    "components": {
                        "event_count": len(event_ids),
                        "event_ids": self._debug_event_ids(event_ids),
                        "rag_chunk_count": len(rag_chunks),
                        "state_delta_count": len(extraction.state_deltas),
                        "pricing_quote_present": pricing_quote is not None,
                        "timeline_estimate_present": timeline_estimate is not None,
                        "portfolio_response_present": portfolio_response is not None,
                    },
                    "recorded_at": datetime.now(UTC).isoformat(),
                }
            )
            return ChatTurnResponse(
                thread_id=thread_id,
                bubbles=bubbles,
                intent=intent,
                language_status=language.language,
                debug_event_ids=self._debug_event_ids(event_ids),
            )

    @staticmethod
    def _apply_sales_action_plan_to_state(
        state: ThreadState,
        action_plan: ActionPlan,
    ) -> None:
        if action_plan.confirmation_required and action_plan.pending_confirmation_key:
            state.sales_actions.pending_confirmation.type = action_plan.pending_confirmation_key
            state.sales_actions.pending_confirmation.payload = action_plan.collected_slots
            state.sales_actions.pending_confirmation.created_at = datetime.now(UTC)

        if action_plan.action_type == ActionType.SCHEDULE_CONSULTATION:
            state.sales_actions.consultation.requested = True
            state.sales_actions.consultation.duration_minutes = int(
                action_plan.collected_slots.get("duration_minutes") or 30
            )
            state.sales_actions.consultation.customer_timezone = _optional_string(
                action_plan.collected_slots.get("customer_timezone")
            )
            state.sales_actions.consultation.preferred_time_window = _optional_string(
                action_plan.collected_slots.get("requested_time_text")
            )
            state.sales_actions.consultation.pending_confirmation = (
                action_plan.status == ActionStatus.NEEDS_CONFIRMATION
            )
            if action_plan.confirmation_required:
                state.sales_actions.consultation.pending_slot = action_plan.collected_slots

        if action_plan.action_type == ActionType.GENERATE_NDA:
            state.sales_actions.documents.nda.requested = True
            state.sales_actions.documents.nda.missing_fields = action_plan.missing_slots
            effective_date = action_plan.collected_slots.get("effective_date")
            if effective_date is not None:
                state.sales_actions.documents.nda.effective_date = str(effective_date)

        if action_plan.action_type == ActionType.GENERATE_AGREEMENT:
            state.sales_actions.documents.agreement.requested = True
            state.sales_actions.documents.agreement.missing_fields = action_plan.missing_slots
            quote_id = action_plan.collected_slots.get("quote_id")
            if quote_id is not None:
                state.sales_actions.documents.agreement.required_quote_id = str(quote_id)

    @staticmethod
    def _sales_action_status_message(
        action_plan: ActionPlan,
        action_result: ActionResult | None,
    ) -> str | None:
        if action_result is not None:
            if action_result.action_type in {
                ActionType.GENERATE_NDA,
                ActionType.GENERATE_AGREEMENT,
                ActionType.SCHEDULE_CONSULTATION,
            }:
                return action_result.customer_safe_summary
            return None

        if action_plan.action_type == ActionType.SCHEDULE_CONSULTATION:
            if action_plan.status == ActionStatus.MISSING_INFO:
                missing = set(action_plan.missing_slots)
                consultation_parts: list[str] = []
                if "name" in missing:
                    consultation_parts.append("your full name")
                if "email_or_phone" in missing:
                    consultation_parts.append("your email or phone number")
                if "preferred_date_or_time_window" in missing:
                    consultation_parts.append("the day and time that works for you")

                if len(consultation_parts) > 1:
                    details = ", ".join(consultation_parts[:-1]) + f", and {consultation_parts[-1]}"
                elif consultation_parts:
                    details = consultation_parts[0]
                else:
                    details = "the missing consultation details"

                return f"I can help schedule a 30-minute consultation. I just need {details}."

            if action_plan.status == ActionStatus.NEEDS_CONFIRMATION:
                name = action_plan.collected_slots.get("name") or "you"
                requested = (
                    action_plan.collected_slots.get("requested_time_text") or "the requested time"
                )
                return (
                    f"I can book a 30-minute consultation for {name}. "
                    f"I’ll check the team in priority order — Jerry Miller, Robert Williams, "
                    f"then Alex Vartan — for {requested}. Should I book it?"
                )

            return None

        if action_plan.action_type == ActionType.GENERATE_AGREEMENT:
            if action_plan.status == ActionStatus.BLOCKED:
                return (
                    "I can prepare the service agreement, but I need to create "
                    "a quote first so the fees, services, and terms are accurate."
                )
            if action_plan.status == ActionStatus.MISSING_INFO:
                missing = set(action_plan.missing_slots)
                agreement_parts: list[str] = []

                if "name" in missing:
                    agreement_parts.append("your full name")
                if "email" in missing:
                    agreement_parts.append("your email")
                if "phone" in missing:
                    agreement_parts.append("your phone number")
                if "client_location" in missing:
                    agreement_parts.append("your city/state or billing location")

                if len(agreement_parts) > 1:
                    details = ", ".join(agreement_parts[:-1]) + f", and {agreement_parts[-1]}"
                elif agreement_parts:
                    details = agreement_parts[0]
                else:
                    details = "the missing agreement details"

                return f"I can prepare the service agreement next. I just need {details}."
            if action_plan.status == ActionStatus.NEEDS_CONFIRMATION:
                name = action_plan.collected_slots.get("name") or "the client"
                email = action_plan.collected_slots.get("email") or "your email"
                return (
                    f"I have the agreement details ready for {name} at {email}. "
                    "Should I send it there?"
                )
            return None

        if action_plan.action_type != ActionType.GENERATE_NDA:
            return None

        if action_plan.status == ActionStatus.MISSING_INFO:
            missing = set(action_plan.missing_slots)
            parts: list[str] = []
            if "name" in missing:
                parts.append("your full name")
            if "email" in missing:
                parts.append("your email")
            if "phone" in missing:
                parts.append("your phone number")
            if "effective_date" in missing:
                parts.append("the NDA effective date")

            if parts:
                if len(parts) == 1:
                    details = parts[0]
                else:
                    details = ", ".join(parts[:-1]) + f", and {parts[-1]}"
            else:
                details = "the missing NDA details"

            return (
                "Absolutely — we can handle confidentiality before you share the "
                f"manuscript. I just need {details} to prepare it."
            )

        if action_plan.status == ActionStatus.NEEDS_CONFIRMATION:
            name = action_plan.collected_slots.get("name") or "the author"
            email = action_plan.collected_slots.get("email") or "your email"
            effective_date = (
                action_plan.collected_slots.get("effective_date") or "the selected effective date"
            )
            return (
                f"I have the NDA details ready for {name} at {email}, effective "
                f"{effective_date}. Should I send it there?"
            )

        return None

    @staticmethod
    def _portfolio_response_from_action(
        action_result: ActionResult | None,
    ) -> PortfolioResponse | None:
        if action_result is None or not action_result.success:
            return None
        if action_result.action_type != ActionType.PORTFOLIO_LOOKUP:
            return None

        payload = action_result.payload
        try:
            return PortfolioResponse.model_validate(
                {
                    "service": payload.get("service"),
                    "requested_genre": payload.get("requested_genre"),
                    "status": payload.get("status"),
                    "samples": payload.get("samples") or [],
                    "message": payload.get("message") or action_result.customer_safe_summary,
                    "registry_version": "portfolio_action",
                    "matched_genre": payload.get("matched_genre"),
                    "fallback_used": bool(payload.get("fallback_used")),
                }
            )
        except Exception as exc:
            structlog.get_logger(__name__).warning(
                "portfolio_action_response_invalid",
                exception_class=exc.__class__.__name__,
            )
            return None

    @staticmethod
    def _apply_sales_action_result_to_state(
        state: ThreadState,
        action_result: ActionResult | None,
    ) -> None:
        if action_result is None or not action_result.success:
            return

        if action_result.action_type == ActionType.CREATE_LEAD:
            lead = action_result.payload.get("lead")
            if not isinstance(lead, dict):
                return

            state.sales_actions.lead.lead_id = str(lead.get("id") or action_result.result_id)
            state.sales_actions.lead.name = _optional_string(lead.get("name"))
            state.sales_actions.lead.email = _optional_string(lead.get("email"))
            state.sales_actions.lead.phone = _optional_string(lead.get("phone"))
            state.sales_actions.lead.preferred_contact_method = _optional_string(
                lead.get("preferred_contact_method")
            )
            state.sales_actions.lead.created = True
            state.sales_actions.lead.last_updated_at = datetime.now(UTC)
            return

        if action_result.action_type == ActionType.PRICE_QUOTE:
            state.sales_actions.pricing.requested = True
            if _pricing_result_is_agreement_ready(action_result.payload):
                state.sales_actions.pricing.quote_id = action_result.result_id
            state.sales_actions.pricing.last_quote_summary = action_result.customer_safe_summary
            missing_fields = action_result.payload.get("missing_fields")
            state.sales_actions.pricing.missing_fields = (
                [str(field) for field in missing_fields] if isinstance(missing_fields, list) else []
            )
            state.sales_actions.pricing.used_default_assumptions = bool(
                action_result.payload.get("used_default_assumptions")
            )
            assumptions = action_result.payload.get("assumptions")
            state.sales_actions.pricing.assumptions = (
                assumptions if isinstance(assumptions, dict) else None
            )
            return

        if action_result.action_type == ActionType.PORTFOLIO_LOOKUP:
            state.sales_actions.portfolio.requested = True
            state.sales_actions.portfolio.requested_service = _optional_string(
                action_result.payload.get("service")
            )
            state.sales_actions.portfolio.genre = _optional_string(
                action_result.payload.get("matched_genre")
                or action_result.payload.get("requested_genre")
            )
            sample_ids = action_result.payload.get("sample_ids")
            if isinstance(sample_ids, list):
                new_ids = [str(sample_id) for sample_id in sample_ids]
                state.sales_actions.portfolio.last_sample_ids = new_ids
                state.sales_actions.portfolio.seen_sample_ids = list(
                    dict.fromkeys([*state.sales_actions.portfolio.seen_sample_ids, *new_ids])
                )
            return

        if action_result.action_type == ActionType.SCHEDULE_CONSULTATION:
            state.sales_actions.consultation.requested = True
            state.sales_actions.consultation.pending_confirmation = False
            state.sales_actions.consultation.pending_slot = None
            state.sales_actions.consultation.confirmed_appointment_id = action_result.result_id
            state.sales_actions.consultation.csr_id = _optional_string(
                action_result.payload.get("csr_id")
            )
            state.sales_actions.consultation.csr_name = _optional_string(
                action_result.payload.get("csr_name")
            )
            state.sales_actions.consultation.customer_timezone = _optional_string(
                action_result.payload.get("customer_timezone")
            )
            state.sales_actions.pending_confirmation.type = None
            state.sales_actions.pending_confirmation.payload = None
            state.sales_actions.pending_confirmation.created_at = None
            state.sales_actions.pending_confirmation.expires_at = None
            return

        if action_result.action_type == ActionType.GENERATE_NDA:
            state.sales_actions.documents.nda.requested = True
            state.sales_actions.documents.nda.document_id = action_result.result_id
            state.sales_actions.documents.nda.delivery_status = _optional_string(
                action_result.payload.get("delivery_status") or action_result.payload.get("status")
            )
            state.sales_actions.documents.nda.missing_fields = []
            state.sales_actions.pending_confirmation.type = None
            state.sales_actions.pending_confirmation.payload = None
            state.sales_actions.pending_confirmation.created_at = None
            state.sales_actions.pending_confirmation.expires_at = None
            return

    def _stabilize_service_context(
        self,
        *,
        intent: IntentVote,
        processed: object,
        state: ThreadState,
    ) -> IntentVote:
        explicit_services = _explicit_services_from_processed(processed)
        if explicit_services:
            return intent

        active_service = _active_service_from_state(state)
        if active_service is None:
            return intent

        if intent.service_primary == active_service:
            return intent

        evidence = list(intent.evidence)
        if "state_service_inertia" not in evidence:
            evidence.append("state_service_inertia")

        return intent.model_copy(
            update={
                "service_primary": active_service,
                # When the current turn has no explicit service, any conflicting
                # service guess is weak inference. Do not keep it as secondary.
                "service_secondary": [],
                "rationale": (f"{intent.rationale} Service focus retained from thread state."),
                "evidence": evidence,
            }
        )

    def _apply_service_focus_to_state(
        self,
        *,
        state: ThreadState,
        processed: object,
        intent: IntentVote,
    ) -> None:
        explicit_services = _explicit_services_from_processed(processed)

        services_to_store = explicit_services
        if not services_to_store and intent.service_primary is not None:
            # Only use inferred service when there is no existing durable service.
            # This prevents a later weak inference from replacing the active thread focus.
            if _active_service_from_state(state) is None:
                services_to_store = [intent.service_primary]

        for service in services_to_store:
            _append_service_focus(state, service)

    async def _build_trg_response_hint(
        self,
        *,
        thread_id: UUID,
        state: ThreadState,
        intent: IntentVote,
    ) -> str | None:
        state_hint = _state_context_response_hint(state, intent)

        if self.trg_engine is None:
            return state_hint

        graph = await self.trg_engine.repository.load(thread_id)
        if graph is None:
            return state_hint

        trg_context = self.trg_engine.build_context(graph)
        parts: list[str] = []

        if state_hint:
            parts.append(state_hint)

        if trg_context.outstanding_questions:
            parts.append(
                "Previous assistant questions already asked: "
                + " | ".join(trg_context.outstanding_questions[-3:])
            )

        if trg_context.repeated_user_messages:
            parts.append(
                "The user appears to be repeating themselves. Do not ask the same "
                "question again; acknowledge the repeated information and move forward."
            )

        if trg_context.contradiction_count:
            parts.append(
                "There may be contradictory project details. Ask one focused "
                "clarifying question instead of assuming."
            )

        if not parts:
            return None

        return " ".join(parts)

    def _record_live_trace(self, row: dict[str, Any]) -> None:
        if self.trace_store is None:
            return

        try:
            self.trace_store.append(row)
        except Exception as exc:
            structlog.get_logger(__name__).warning(
                "live_trace_write_failed",
                exception_class=exc.__class__.__name__,
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


def _trimatch_shortcut_considered_payload(
    *,
    extra_shortcut: object,
    final_intent: IntentVote,
) -> dict[str, Any]:
    extra_snapshot = _trimatch_snapshot(extra_shortcut)
    final_snapshot = _intent_snapshot(final_intent)
    safety = _shortcut_safety_snapshot(
        extra_snapshot=extra_snapshot,
        final_snapshot=final_snapshot,
    )
    shortcut = _shortcut_candidate_decision(
        extra_shortcut=extra_shortcut,
        extra_snapshot=extra_snapshot,
        final_snapshot=final_snapshot,
        safety=safety,
    )

    return {
        "extra_shortcut": extra_snapshot,
        "final": final_snapshot,
        "shortcut": shortcut,
        "safety": safety,
    }


def _shortcut_candidate_decision(
    *,
    extra_shortcut: object,
    extra_snapshot: dict[str, Any] | None,
    final_snapshot: dict[str, Any],
    safety: dict[str, Any],
) -> dict[str, Any]:
    dimension, recommended_value = _shortcut_recommendation(extra_snapshot)
    evidence = _shortcut_evidence(extra_shortcut)
    primary_evidence = _primary_shortcut_evidence(evidence)
    rule_id = primary_evidence.get("rule_id") if isinstance(primary_evidence, dict) else None

    blocked_reasons = _shortcut_blocked_reasons(
        dimension=dimension,
        recommended_value=recommended_value,
        evidence=evidence,
        final_snapshot=final_snapshot,
        safety=safety,
    )
    eligible = not blocked_reasons

    return {
        "eligible": eligible,
        "applied": eligible,
        "dimension": dimension if eligible else None,
        "recommended_value": recommended_value if eligible else None,
        "rule_id": rule_id if eligible and isinstance(rule_id, str) else None,
        "reason": (
            "applied: safe shortcut resolved eligible intent recommendation"
            if eligible
            else "blocked: " + "; ".join(blocked_reasons)
        ),
        "blocked_reasons": blocked_reasons,
    }


def _apply_shortcut_to_intent(
    *,
    intent: IntentVote,
    shortcut: dict[str, Any],
) -> IntentVote:
    if shortcut.get("applied") is not True:
        return intent

    dimension = shortcut.get("dimension")
    recommended_value = shortcut.get("recommended_value")
    rule_id = shortcut.get("rule_id")
    if not isinstance(dimension, str) or not isinstance(recommended_value, str):
        return intent
    if not isinstance(rule_id, str) or not rule_id:
        return intent

    updates: dict[str, Any] = {}
    try:
        if dimension == "query_primary":
            updates["query_primary"] = QueryIntentType(recommended_value)
        elif dimension == "service_primary":
            updates["service_primary"] = ServiceCategory(recommended_value)
        else:
            return intent
    except ValueError:
        return intent

    evidence = [
        *intent.evidence,
        f"trimatch shortcut applied {dimension}={recommended_value} rule_id={rule_id}",
    ]
    rationale = f"{intent.rationale} Shortcut applied safely for {dimension}={recommended_value}."

    return intent.model_copy(
        update={
            **updates,
            "evidence": evidence,
            "rationale": rationale,
        }
    )


def _primary_shortcut_evidence(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    for item in evidence:
        if (
            item.get("layer") in {"exact", "regex"}
            and item.get("shortcut_eligible") is True
            and item.get("negated") is not True
            and item.get("counterfactual") is not True
        ):
            return item
    return evidence[0] if evidence else {}


def _shortcut_recommendation(
    extra_snapshot: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    if extra_snapshot is None:
        return None, None

    for dimension in ("query_primary", "service_primary"):
        value = extra_snapshot.get(dimension)
        if isinstance(value, str) and value:
            return dimension, value

    return None, None


def _shortcut_evidence(extra_shortcut: object) -> list[dict[str, Any]]:
    evidence = getattr(extra_shortcut, "evidence", [])
    if not isinstance(evidence, list):
        return []

    snapshots: list[dict[str, Any]] = []
    for item in evidence:
        if hasattr(item, "model_dump"):
            raw = item.model_dump(mode="json")
        elif isinstance(item, dict):
            raw = item
        else:
            continue

        if isinstance(raw, dict):
            snapshots.append(raw)

    return snapshots


def _shortcut_blocked_reasons(
    *,
    dimension: str | None,
    recommended_value: str | None,
    evidence: list[dict[str, Any]],
    final_snapshot: dict[str, Any],
    safety: dict[str, Any],
) -> list[str]:
    blocked: list[str] = []

    if dimension is None or recommended_value is None:
        blocked.append("no extra shortcut recommendation")
        return blocked

    if dimension not in {"query_primary", "service_primary"}:
        blocked.append(f"unsupported dimension: {dimension}")

    if recommended_value == final_snapshot.get(dimension):
        blocked.append("recommendation already matches final intent")

    if recommended_value in {
        "pricing_question",
        "timeline_question",
        "portfolio_request",
        "nda_request",
        "agreement_request",
        "payment_question",
        "complaint_or_objection",
        "ready_to_buy",
        "spam_or_abuse",
        "off_topic",
    }:
        blocked.append(f"forbidden recommended value: {recommended_value}")

    if _sensitive_shortcut_safety_blocked(safety):
        blocked.append("safety-sensitive intent cannot use shortcut")

    if not evidence:
        blocked.append("no shortcut evidence")

    allowed_evidence = [
        item
        for item in evidence
        if item.get("layer") in {"exact", "regex"}
        and item.get("shortcut_eligible") is True
        and item.get("negated") is not True
        and item.get("counterfactual") is not True
    ]

    if not allowed_evidence:
        blocked.append("no exact or regex shortcut-eligible evidence")

    if allowed_evidence and not any(
        isinstance(item.get("rule_id"), str) and item.get("rule_id") for item in allowed_evidence
    ):
        blocked.append("missing shortcut rule_id")

    if any(item.get("layer") in {"semantic", "fuzzy"} for item in evidence):
        blocked.append("semantic or fuzzy evidence cannot shortcut")

    if any(item.get("shortcut_eligible") is not True for item in evidence):
        blocked.append("shortcut_allowed false or missing on evidence")

    if any(item.get("negated") is True for item in evidence):
        blocked.append("negated evidence cannot shortcut")

    if any(item.get("counterfactual") is True for item in evidence):
        blocked.append("counterfactual evidence cannot shortcut")

    return blocked


def _sensitive_shortcut_safety_blocked(safety: dict[str, Any]) -> bool:
    return any(
        bool(safety.get(key))
        for key in (
            "pricing_sensitive",
            "document_sensitive",
            "portfolio_sensitive",
            "negated",
            "counterfactual",
            "side_effects_allowed",
        )
    )


def _shortcut_safety_snapshot(
    *,
    extra_snapshot: dict[str, Any] | None,
    final_snapshot: dict[str, Any],
) -> dict[str, Any]:
    extra_query = extra_snapshot.get("query_primary") if extra_snapshot else None
    final_query = final_snapshot.get("query_primary")

    pricing_sensitive = extra_query in {
        "pricing_question",
        "timeline_question",
        "payment_question",
    } or final_query in {
        "pricing_question",
        "timeline_question",
        "payment_question",
    }
    document_sensitive = extra_query in {
        "nda_request",
        "agreement_request",
    } or final_query in {
        "nda_request",
        "agreement_request",
    }
    portfolio_sensitive = extra_query == "portfolio_request" or final_query == "portfolio_request"

    return {
        "pricing_sensitive": pricing_sensitive,
        "document_sensitive": document_sensitive,
        "portfolio_sensitive": portfolio_sensitive,
        "negated": False,
        "counterfactual": False,
        "side_effects_allowed": False,
    }


def _trimatch_tiebreaker_considered_payload(
    *,
    active_trimatch: object | None,
    extra_tiebreaker: object,
    ensemble_intent: IntentVote,
    final_intent: IntentVote,
) -> dict[str, Any]:
    active_snapshot = _trimatch_snapshot(active_trimatch)
    extra_snapshot = _trimatch_snapshot(extra_tiebreaker)
    ensemble_snapshot = _intent_snapshot(ensemble_intent)
    final_snapshot = _intent_snapshot(final_intent)
    safety = _tiebreaker_safety_snapshot(
        extra_snapshot=extra_snapshot,
        final_snapshot=final_snapshot,
    )
    decision = _tiebreaker_candidate_decision(
        active_snapshot=active_snapshot,
        extra_snapshot=extra_snapshot,
        ensemble_snapshot=ensemble_snapshot,
        final_snapshot=final_snapshot,
        safety=safety,
    )

    return {
        "extra_tiebreaker": extra_snapshot,
        "before": {
            "active_trimatch": active_snapshot,
            "ensemble": ensemble_snapshot,
            "final_before_tiebreaker": final_snapshot,
        },
        "decision": decision,
        "safety": safety,
    }


def _apply_tiebreaker_to_intent(
    *,
    intent: IntentVote,
    decision: dict[str, Any],
) -> IntentVote:
    if decision.get("applied") is not True:
        return intent

    dimension = decision.get("dimension")
    recommended_value = decision.get("recommended_value")
    if not isinstance(dimension, str) or not isinstance(recommended_value, str):
        return intent

    updates: dict[str, Any] = {}
    try:
        if dimension == "query_primary":
            updates["query_primary"] = QueryIntentType(recommended_value)
        elif dimension == "service_primary":
            updates["service_primary"] = ServiceCategory(recommended_value)
        else:
            return intent
    except ValueError:
        return intent

    evidence = [
        *intent.evidence,
        f"trimatch tiebreaker applied {dimension}={recommended_value}",
    ]
    rationale = f"{intent.rationale} Tiebreaker applied safely for {dimension}={recommended_value}."

    return intent.model_copy(
        update={
            **updates,
            "evidence": evidence,
            "rationale": rationale,
        }
    )


def _tiebreaker_candidate_decision(
    *,
    active_snapshot: dict[str, Any] | None,
    extra_snapshot: dict[str, Any] | None,
    ensemble_snapshot: dict[str, Any],
    final_snapshot: dict[str, Any],
    safety: dict[str, Any],
) -> dict[str, Any]:
    dimension, recommended_value = _tiebreaker_recommendation(extra_snapshot)

    blocked_reasons = _tiebreaker_blocked_reasons(
        active_snapshot=active_snapshot,
        ensemble_snapshot=ensemble_snapshot,
        final_snapshot=final_snapshot,
        safety=safety,
        dimension=dimension,
        recommended_value=recommended_value,
    )
    eligible = not blocked_reasons

    return {
        "eligible": eligible,
        "applied": eligible,
        "dimension": dimension if eligible else None,
        "recommended_value": recommended_value if eligible else None,
        "reason": (
            "applied: safe tiebreaker resolved eligible intent disagreement"
            if eligible
            else "blocked: " + "; ".join(blocked_reasons)
        ),
        "blocked_reasons": blocked_reasons,
    }


def _tiebreaker_recommendation(
    extra_snapshot: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    if extra_snapshot is None:
        return None, None

    for dimension in ("query_primary", "service_primary"):
        value = extra_snapshot.get(dimension)
        if isinstance(value, str) and value:
            return dimension, value

    return None, None


def _tiebreaker_blocked_reasons(
    *,
    active_snapshot: dict[str, Any] | None,
    ensemble_snapshot: dict[str, Any],
    final_snapshot: dict[str, Any],
    safety: dict[str, Any],
    dimension: str | None,
    recommended_value: str | None,
) -> list[str]:
    blocked: list[str] = []

    if dimension is None or recommended_value is None:
        blocked.append("no extra tiebreaker recommendation")
        return blocked

    if dimension not in {"query_primary", "service_primary"}:
        blocked.append(f"unsupported dimension: {dimension}")

    if recommended_value == final_snapshot.get(dimension):
        blocked.append("recommendation already matches final intent")

    if not _has_qualifying_disagreement(
        active_snapshot=active_snapshot,
        ensemble_snapshot=ensemble_snapshot,
        final_snapshot=final_snapshot,
        dimension=dimension,
        recommended_value=recommended_value,
    ):
        blocked.append("no qualifying extra/final disagreement")

    extra_confidence = _float_or_zero(
        active_snapshot.get("confidence") if active_snapshot else None
    )
    if extra_confidence and extra_confidence < 0.0:
        blocked.append("invalid active confidence")

    if _float_or_zero(final_snapshot.get("confidence")) > 0.72:
        blocked.append("final confidence above tiebreaker threshold")

    if _sensitive_safety_blocked(safety):
        blocked.append("safety-sensitive intent cannot use tiebreaker")

    if recommended_value in {
        "pricing_question",
        "timeline_question",
        "portfolio_request",
        "nda_request",
        "agreement_request",
        "payment_question",
        "complaint_or_objection",
        "ready_to_buy",
        "spam_or_abuse",
        "off_topic",
    }:
        blocked.append(f"forbidden recommended value: {recommended_value}")

    return blocked


def _has_qualifying_disagreement(
    *,
    active_snapshot: dict[str, Any] | None,
    ensemble_snapshot: dict[str, Any],
    final_snapshot: dict[str, Any],
    dimension: str,
    recommended_value: str,
) -> bool:
    comparison_values = {
        active_snapshot.get(dimension) if active_snapshot else None,
        ensemble_snapshot.get(dimension),
        final_snapshot.get(dimension),
    }
    non_empty_values = {value for value in comparison_values if value is not None}
    return bool(non_empty_values) and any(value != recommended_value for value in non_empty_values)


def _sensitive_safety_blocked(safety: dict[str, Any]) -> bool:
    return any(
        bool(safety.get(key))
        for key in (
            "pricing_sensitive",
            "document_sensitive",
            "portfolio_sensitive",
            "negated",
            "counterfactual",
            "side_effects_allowed",
        )
    )


def _float_or_zero(value: object) -> float:
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _tiebreaker_safety_snapshot(
    *,
    extra_snapshot: dict[str, Any] | None,
    final_snapshot: dict[str, Any],
) -> dict[str, Any]:
    extra_query = extra_snapshot.get("query_primary") if extra_snapshot else None
    final_query = final_snapshot.get("query_primary")

    pricing_sensitive = extra_query in {
        "pricing_question",
        "timeline_question",
        "payment_question",
    } or final_query in {
        "pricing_question",
        "timeline_question",
        "payment_question",
    }
    document_sensitive = extra_query in {
        "nda_request",
        "agreement_request",
    } or final_query in {
        "nda_request",
        "agreement_request",
    }
    portfolio_sensitive = extra_query == "portfolio_request" or final_query == "portfolio_request"

    return {
        "pricing_sensitive": pricing_sensitive,
        "document_sensitive": document_sensitive,
        "portfolio_sensitive": portfolio_sensitive,
        "negated": False,
        "counterfactual": False,
        "side_effects_allowed": False,
    }


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


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _pricing_result_is_agreement_ready(payload: dict[str, Any]) -> bool:
    status = str(payload.get("status") or "")
    if status not in {"estimated", "formal_quote_ready", "accepted"}:
        return False

    missing_fields = payload.get("missing_fields")
    if isinstance(missing_fields, list) and missing_fields:
        return False

    quote_output = payload.get("quote_output")
    if not isinstance(quote_output, dict):
        return False

    return _quote_output_has_nonzero_total(quote_output)


def _quote_output_has_nonzero_total(quote_output: dict[str, Any]) -> bool:
    range_value = quote_output.get("total_price_range") or quote_output.get("subtotal_range")
    if isinstance(range_value, dict):
        return bool(
            _money_like_amount(range_value.get("low"))
            or _money_like_amount(range_value.get("high"))
        )

    for key in (
        "final_fee",
        "total_fee",
        "estimated_total",
        "total",
        "price",
        "amount",
    ):
        if _money_like_amount(quote_output.get(key)):
            return True

    for value in quote_output.values():
        if isinstance(value, dict) and _quote_output_has_nonzero_total(value):
            return True

    return False


def _money_like_amount(value: object) -> float | None:
    if isinstance(value, dict):
        return _money_like_amount(value.get("amount"))

    if isinstance(value, (int, float)) and value > 0:
        return float(value)

    if isinstance(value, str):
        cleaned = value.replace("$", "").replace(",", "").strip()
        try:
            amount = float(cleaned)
        except ValueError:
            return None
        return amount if amount > 0 else None

    return None


def _explicit_services_from_processed(processed: object) -> list[ServiceCategory]:
    atoms = getattr(processed, "deterministic_atoms", {}) or {}
    raw_services = atoms.get("services")

    if not isinstance(raw_services, list):
        return []

    services: list[ServiceCategory] = []
    for raw_service in raw_services:
        if not isinstance(raw_service, str):
            continue
        try:
            service = ServiceCategory(raw_service)
        except ValueError:
            continue
        if service not in services:
            services.append(service)

    return services


def _active_service_from_state(state: ThreadState) -> ServiceCategory | None:
    for interest in reversed(state.project.services_discussed):
        raw_service = interest.service.value
        if isinstance(raw_service, ServiceCategory):
            return raw_service
        if isinstance(raw_service, str):
            try:
                return ServiceCategory(raw_service)
            except ValueError:
                continue

    return None


def _append_service_focus(state: ThreadState, service: ServiceCategory) -> None:
    existing = {
        interest.service.value
        for interest in state.project.services_discussed
        if interest.service.value is not None
    }

    if service in existing or service.value in existing:
        return

    state.project.services_discussed.append(
        ServiceInterest(
            service=FieldMeta(
                value=service,
                confidence=0.94,
                source=Source.USER_STATED,
                extracted_at=datetime.now(UTC),
                extracted_by="deterministic_service_focus.v1",
                raw_excerpt=service.value,
            ),
            confidence=0.94,
        )
    )


def _state_context_response_hint(state: ThreadState, intent: IntentVote) -> str | None:
    known: list[str] = []
    missing: list[str] = []

    if state.project.manuscript_status.value is not None:
        known.append(f"manuscript status is {state.project.manuscript_status.value}")
    else:
        missing.append("manuscript stage")

    if state.project.genre.value:
        known.append(f"genre is {state.project.genre.value}")
    else:
        missing.append("genre")

    if state.project.word_count.value is not None:
        known.append(f"word count is {state.project.word_count.value}")
    elif state.project.page_count.value is not None:
        known.append(f"page count is {state.project.page_count.value}")
    else:
        missing.append("word or page count")

    if not known:
        return None

    hint = "Known project facts: " + "; ".join(known) + ". Do not ask again for these known facts."

    if missing:
        hint += " Still missing: " + ", ".join(missing) + "."

    active_service = _active_service_from_state(state)
    if active_service is not None:
        hint += f" Current service focus: {active_service.value}."
    elif intent.service_primary is not None:
        hint += f" Current service focus: {intent.service_primary.value}."

    return hint
