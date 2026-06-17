from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import structlog
from prometheus_client import Counter, Histogram
from pydantic import BaseModel

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
from bookcraft.components.attachments.assessment_priority import AttachmentAssessmentPriority
from bookcraft.components.attachments.intake import (
    AttachmentIntakeProcessor,
    AttachmentIntakeResult,
)
from bookcraft.components.complaints import ComplaintClassifier
from bookcraft.components.context import ContextEnforcementGate, ContextPackBuilder
from bookcraft.components.context.entity_index import ConversationEntityIndex
from bookcraft.components.context.project_manager import (
    ProjectContextManager,
    ProjectContextSnapshot,
)
from bookcraft.components.context.slot_tracker import SlotTracker
from bookcraft.components.extraction import CombinedExtractor, StateApplier
from bookcraft.components.extraction.llm_extractor import LLMMetadataExtractor
from bookcraft.components.extraction.schemas import CombinedExtraction, StateDelta
from bookcraft.components.intent import EnsembleIntentClassifier
from bookcraft.components.intent.context_arbiter import ContextArbiter
from bookcraft.components.intent.flexible_router import FlexibleIntentDecision, FlexibleIntentRouter
from bookcraft.components.intent.hardening import harden_intent_from_message
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.language_guard import LanguageGuard
from bookcraft.components.leads import (
    ContactCaptureDetector,
    LeadIntakePayload,
    LeadObjectiveEngine,
)
from bookcraft.components.leads.contact_recovery import (
    user_claims_already_shared,
    user_has_complaint_or_privacy_concern,
    user_objects_to_pii_misuse,
)
from bookcraft.components.leads.contact_utils import contact_is_ready, is_real_contact_value
from bookcraft.components.metadata import ServiceMetadataExtractor
from bookcraft.components.persona import BookCraftPersona
from bookcraft.components.portfolio import (
    PortfolioEngine,
    PortfolioFallbackDecision,
    PortfolioFallbackPolicy,
    PortfolioRequest,
    PortfolioResponse,
)
from bookcraft.components.portfolio.fallback_policy import update_portfolio_filter_state
from bookcraft.components.portfolio.rich_segments import PortfolioRichSegmentBuilder
from bookcraft.components.preprocessor import SharedPreprocessor
from bookcraft.components.pricing import (
    PricingQuoteRequest,
    PricingTimelineEngine,
    PricingTimelineQuote,
)
from bookcraft.components.rag.query_builder import RAGQueryBuilder
from bookcraft.components.rag.retriever import RagRetriever
from bookcraft.components.rag.schemas import RetrievedChunk
from bookcraft.components.response import ResponseFormatter, SonnetResponseGenerator
from bookcraft.components.response.contracts import CustomerResponseContract
from bookcraft.components.response.planner import ResponsePlanner
from bookcraft.components.response.quality_gate import ResponseQualityGate
from bookcraft.components.response.schemas import ResponseDraft
from bookcraft.components.response.style_policy import ResponseStylePolicy
from bookcraft.components.safety import InputSafetyGuard
from bookcraft.components.sales import (
    AnswerBeforeCapturePolicy,
    ConsultationObjectiveEngine,
    CurrentQuestionPriorityDetector,
)
from bookcraft.components.sales.consultation_state import (
    ConsultationStage,
    ConsultationStateDecision,
    reduce_consultation_state,
)
from bookcraft.components.service_workflow import (
    ServiceWorkflow,
    is_sequencing_question,
    resolve_service_aliases,
)
from bookcraft.components.storage.events import calculate_event_hash
from bookcraft.components.storage.thread_repository import (
    LoadedThread,
    ThreadRepository,
    ThreadVersionConflictError,
)
from bookcraft.components.tools.governance import ToolGovernanceGate
from bookcraft.components.csr.commitment_detector import detect_commitments
from bookcraft.components.csr.summarizer import CsrContextSummarizer
from bookcraft.components.trg import TemporalRelationGraphEngine
from bookcraft.components.trg.checkpointer import ConversationCheckpointer
from bookcraft.components.trg.fact_store import TRGFactStore
from bookcraft.components.trg.schemas import (
    DelegationEvent,
    ProjectShiftEvent,
    SlotResolutionEvent,
    TemporalRelationGraph,
    TRGContext,
)
from bookcraft.components.trimatch import TriMatchEngine
from bookcraft.components.trimatch.schemas import TriMatchResult
from bookcraft.domain.enums import QueryIntentType, ServiceCategory, Source
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ServiceInterest, ThreadState
from bookcraft.infra.observability import STAGE_LATENCY
from bookcraft.infra.redaction import redact_mapping, redact_text
from bookcraft.infra.trace_sanitizer import (
    safe_contact_capture,
    safe_lead_intake,
    sanitize_event_payload,
)
from bookcraft.tools import ToolContext, ToolDispatcher

if TYPE_CHECKING:
    from bookcraft.api.chat import ChatTurnRequest, ChatTurnResponse

CHAT_TURNS_TOTAL = Counter("chatbot_turns_total", "Chat turns handled.")
CHAT_TURN_LATENCY = Histogram("chatbot_turn_latency_seconds", "Chat turn latency.")
STATE_UPDATES = Counter("thread_state_updates_total", "Thread state updates.", ["result"])
ENTITY_INDEX_FAILURES = Counter(
    "entity_index_failures_total",
    "Entity index upsert failures.",
    ["kind"],
)

_IMMEDIATE_EVENT_TYPES: frozenset[str] = frozenset({"user.message"})


@dataclass
class _EventBuffer:
    events: list[dict[str, object]] = field(default_factory=list)

    def collect(
        self,
        *,
        thread_id: UUID,
        sequence: int,
        event_type: str,
        payload: dict[str, object],
        previous_hash: str | None,
    ) -> tuple[str, int, str]:
        from bookcraft.components.storage.events import calculate_event_hash
        event_hash = calculate_event_hash(
            thread_id=thread_id,
            sequence=sequence + 1,
            event_type=event_type,
            payload=payload,
            previous_hash=previous_hash,
        )
        self.events.append({
            "sequence": sequence + 1,
            "event_type": event_type,
            "payload": payload,
            "previous_hash": previous_hash,
            "event_hash": event_hash,
        })
        return event_hash, sequence + 1, event_hash


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
    context_pack_builder: ContextPackBuilder = field(default_factory=ContextPackBuilder)
    context_arbiter: ContextArbiter = field(default_factory=ContextArbiter)
    tool_governance_gate: ToolGovernanceGate = field(default_factory=ToolGovernanceGate)
    response_planner: ResponsePlanner = field(default_factory=ResponsePlanner)
    response_quality_gate: ResponseQualityGate = field(default_factory=ResponseQualityGate)
    response_style_policy: ResponseStylePolicy = field(default_factory=ResponseStylePolicy.default)
    rag_query_builder: RAGQueryBuilder = field(default_factory=RAGQueryBuilder)
    project_context_manager: ProjectContextManager = field(default_factory=ProjectContextManager)
    slot_tracker: SlotTracker = field(default_factory=SlotTracker)
    portfolio_fallback_policy: PortfolioFallbackPolicy = field(
        default_factory=PortfolioFallbackPolicy
    )
    flexible_intent_router: FlexibleIntentRouter = field(default_factory=FlexibleIntentRouter)
    attachment_intake_processor: AttachmentIntakeProcessor = field(
        default_factory=AttachmentIntakeProcessor
    )
    contact_capture_detector: ContactCaptureDetector = field(default_factory=ContactCaptureDetector)
    lead_objective_engine: LeadObjectiveEngine = field(default_factory=LeadObjectiveEngine)
    # PR 2: consultation-first sales planner engines.
    current_question_priority_detector: CurrentQuestionPriorityDetector = field(
        default_factory=CurrentQuestionPriorityDetector
    )
    answer_before_capture_policy: AnswerBeforeCapturePolicy = field(
        default_factory=AnswerBeforeCapturePolicy
    )
    consultation_require_phone: bool = True
    consultation_objective_engine: ConsultationObjectiveEngine = field(
        default_factory=ConsultationObjectiveEngine
    )
    # PR 3: attachment assessment priority + portfolio rich links.
    attachment_assessment_priority: AttachmentAssessmentPriority = field(
        default_factory=AttachmentAssessmentPriority
    )
    portfolio_rich_segment_builder: PortfolioRichSegmentBuilder = field(
        default_factory=PortfolioRichSegmentBuilder
    )
    # PR 4: input safety guard + service metadata extractor.
    input_safety_guard: InputSafetyGuard = field(default_factory=InputSafetyGuard)
    service_metadata_extractor: ServiceMetadataExtractor = field(
        default_factory=ServiceMetadataExtractor
    )
    # Context enforcement gate (PR: context-enforcement-correction-recovery).
    context_enforcement_gate: ContextEnforcementGate = field(default_factory=ContextEnforcementGate)
    # Batch 4: complaint classifier — detects frustration/PII/handoff signals.
    complaint_classifier: ComplaintClassifier = field(default_factory=ComplaintClassifier)
    # Persona: manages the BookCraft representative identity per thread.
    persona: BookCraftPersona = field(default_factory=BookCraftPersona)
    # Service workflow: predecessor/successor/parallel sequencing advisor.
    service_workflow: ServiceWorkflow = field(default_factory=ServiceWorkflow)
    # LLM metadata extractor — runs synchronously before response generation.
    llm_metadata_extractor: LLMMetadataExtractor | None = None
    # PostgreSQL persistence for TRG facts + milestone checkpoints.
    trg_fact_store: TRGFactStore | None = None
    conversation_checkpointer: ConversationCheckpointer | None = None
    # ES entity memory index — entity-centric semantic memory per conversation.
    entity_index: ConversationEntityIndex | None = None
    # CSR context ingestion components.
    csr_summarizer: CsrContextSummarizer = field(default_factory=CsrContextSummarizer)
    threads: dict[UUID, ThreadMemory] = field(default_factory=dict)
    thread_repository: ThreadRepository | None = None
    environment: str = "dev"
    response_repair_enabled: bool = False
    event_log_batching_enabled: bool = False
    trg_background_persist_enabled: bool = False
    llm_extraction_overlap_enabled: bool = False
    trimatch_event_evidence_summary: bool = False
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
            _evt_buf: _EventBuffer | None = _EventBuffer() if self.event_log_batching_enabled and self.thread_repository is not None else None
            _preloaded_trg_graph: TemporalRelationGraph | None = None

            # PR 4: run input safety guard before any processing.
            input_safety_decision = self.input_safety_guard.evaluate(payload.message, state=state)
            if input_safety_decision.action in {"warn", "block"}:
                safety_event = InputSafetyGuard.build_safety_event(
                    payload.message, input_safety_decision
                )
                state.safety_events = list(state.safety_events) + [safety_event]
                # Persist safety event in-memory so repeated hostility detection works.
                if thread_id in self.threads:
                    self.threads[thread_id].state = state

            if input_safety_decision.action == "block":
                event_id, event_sequence, previous_event_hash = await self._append_thread_event(
                    thread_id=thread_id,
                    sequence=event_sequence,
                    previous_hash=previous_event_hash,
                    event_type="user.message",
                    payload={"text": payload.message},
                    _buf=_evt_buf,
                )
                event_ids = [event_id]
                self._record_live_trace(
                    {
                        "thread_id": str(thread_id),
                        "customer_id": str(payload.customer_id)
                        if payload.customer_id is not None
                        else None,
                        "correlation_id": payload.correlation_id,
                        "message_preview": redact_text(payload.message)[:500],
                        "elapsed_ms": round((time.perf_counter() - turn_started) * 1000, 2),
                        "language_status": "en",
                        "input_safety": input_safety_decision.model_dump(mode="json"),
                        "assistant": {
                            "source": "input_safety_guard",
                            "bubble_count": 0,
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
                    bubbles=[],
                    intent=None,
                    language_status="en",
                    debug_event_ids=self._debug_event_ids(event_ids),
                    blocked=True,
                    input_disabled=input_safety_decision.input_disabled,
                    system_message=input_safety_decision.system_message,
                )

            language = self.language_guard.detect(payload.message, cached_language="en")
            event_id, event_sequence, previous_event_hash = await self._append_thread_event(
                thread_id=thread_id,
                sequence=event_sequence,
                previous_hash=previous_event_hash,
                event_type="user.message",
                # Step 2 (Batch 1): sanitize event payload before persistence.
                payload=sanitize_event_payload("user.message", {"text": payload.message}),
                _buf=_evt_buf,
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
                    _buf=_evt_buf,
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
                    payload=_summarize_trimatch_payload(trimatch_result) if self.trimatch_event_evidence_summary else trimatch_result.model_dump(mode="json"),
                    _buf=_evt_buf,
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
                            payload=_summarize_trimatch_payload(trimatch_shadow_result) if self.trimatch_event_evidence_summary else trimatch_shadow_result.model_dump(mode="json"),
                            _buf=_evt_buf,
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
                        _buf=_evt_buf,
                    )
                    event_ids.append(event_id)

            with STAGE_LATENCY.labels(stage="intent_classification").time():
                intent = await self.intent_classifier.classify(
                    processed,
                    state,
                    trimatch_result=trimatch_result,
                )
            ensemble_intent = intent
            intent = harden_intent_from_message(intent, processed)
            arbiter_result = self.context_arbiter.arbitrate(
                intent=intent,
                processed=processed,
                state=state,
            )
            intent = arbiter_result.intent
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
                    _buf=_evt_buf,
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
                    _buf=_evt_buf,
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
                    _buf=_evt_buf,
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
                    _buf=_evt_buf,
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
                _buf=_evt_buf,
            )
            event_ids.append(event_id)
            # Phase 13: attachment intake.
            _raw_attachments = list(getattr(payload, "attachments", None) or [])
            _attachment_parse_error: str | None = None
            try:
                attachment_intake_result = self.attachment_intake_processor.process(
                    attachments=_raw_attachments if _raw_attachments else None,
                    message=payload.message,
                    active_service=None,  # re-enriched below after extraction (Step 8)
                    manuscript_status=None,
                )
            except Exception as _att_exc:  # noqa: BLE001
                # Step 9: surface attachment errors safely — do not swallow silently.
                _attachment_parse_error = type(_att_exc).__name__
                attachment_intake_result = AttachmentIntakeResult(
                    audit=[f"attachment_parse_error:{_attachment_parse_error}"]
                )

            with STAGE_LATENCY.labels(stage="extraction").time():
                extraction = await self.extractor.extract(processed, state)
                # Step 6: collect rejected delta paths for trace.
                _rejected_delta_paths: list[str] = []
                previous_state = state.model_copy(deep=True)
                state = self.state_applier.apply(
                    state, extraction, rejected_paths=_rejected_delta_paths
                )
                self._apply_service_focus_to_state(
                    state=state,
                    processed=processed,
                    intent=intent,
                )

            # LLM metadata extraction — synchronous, runs before response generation so the
            # current response already benefits from richer facts (book title, cover preferences,
            # section structure, page dimensions, etc.) that regex cannot capture.
            _llm_extraction_delta_count = 0
            _llm_extraction_task: asyncio.Task | None = None
            if self.llm_metadata_extractor is not None and self.llm_extraction_overlap_enabled:
                _llm_extraction_task = asyncio.create_task(
                    self.llm_metadata_extractor.extract(
                        user_text=payload.message,
                        assistant_text=state.last_assistant_text or "",
                        state=state.model_copy(deep=True),
                    )
                )
            if self.llm_metadata_extractor is not None and not self.llm_extraction_overlap_enabled:
                try:
                    _llm_result = await self.llm_metadata_extractor.extract(
                        user_text=payload.message,
                        assistant_text=state.last_assistant_text or "",
                        state=state,
                    )
                    if _llm_result.state_deltas:
                        _llm_extraction_delta_count = len(_llm_result.state_deltas)
                        _llm_combined = CombinedExtraction(
                            state_deltas=_llm_result.state_deltas
                        )
                        state = self.state_applier.apply(
                            state, _llm_combined, rejected_paths=_rejected_delta_paths
                        )
                        # Propagate deltas to deterministic extraction so TRG sees them.
                        extraction.state_deltas.extend(_llm_result.state_deltas)
                    if _llm_result.rich_metadata:
                        if "book_specs" not in state.service_metadata:
                            state.service_metadata["book_specs"] = {}
                        for _k, _v in _llm_result.rich_metadata.items():
                            if _k not in state.service_metadata["book_specs"]:
                                state.service_metadata["book_specs"][_k] = _v
                    # Upsert extracted facts into ES entity memory index.
                    if self.entity_index is not None:
                        for _delta in _llm_result.state_deltas:
                            try:
                                await self.entity_index.upsert_structured_fact(
                                    thread_id=thread_id,
                                    entity_path=_delta.path,
                                    entity_value=str(_delta.value),
                                    confidence=_delta.confidence,
                                    source_extraction=True,
                                    turn_index=event_sequence,
                                )
                            except Exception:  # noqa: BLE001
                                ENTITY_INDEX_FAILURES.labels(kind="structured_fact").inc()
                        for _rk, _rv in _llm_result.rich_metadata.items():
                            try:
                                await self.entity_index.upsert_free_text_fact(
                                    thread_id=thread_id,
                                    key=_rk,
                                    value=_rv,
                                    confidence=0.9,
                                    turn_index=event_sequence,
                                )
                            except Exception:  # noqa: BLE001
                                ENTITY_INDEX_FAILURES.labels(kind="free_text_fact").inc()
                except Exception as _llm_exc:  # noqa: BLE001
                    structlog.get_logger(__name__).warning(
                        "llm_metadata_extraction_failed",
                        thread_id=str(thread_id),
                        exception_class=_llm_exc.__class__.__name__,
                    )

            # Step 8: re-enrich attachment intake now that active service / status is known.
            if _raw_attachments and attachment_intake_result.attachments:
                _known_svc = (
                    state.project.services_discussed[-1].service.value
                    if state.project.services_discussed
                    else None
                )
                _known_ms = (
                    str(state.project.manuscript_status.value)
                    if state.project.manuscript_status.value
                    else None
                )
                try:
                    attachment_intake_result = self.attachment_intake_processor.process(
                        attachments=_raw_attachments,
                        message=payload.message,
                        active_service=_known_svc,
                        manuscript_status=_known_ms,
                    )
                except Exception as _att_exc2:  # noqa: BLE001
                    _attachment_parse_error = type(_att_exc2).__name__

            if attachment_intake_result.attachments:
                state.attachments_received = [
                    a.model_dump(mode="json") for a in attachment_intake_result.attachments
                ]
                state.latest_assessment_type = attachment_intake_result.assessment_type
                state.latest_specialist_role = attachment_intake_result.specialist_role
            contact_capture = self.contact_capture_detector.extract(payload.message)
            contact_capture = self.contact_capture_detector.merge_with_state(contact_capture, state)
            # BUG-023: full overwrite loses previously-captured fields that were
            # redacted in persisted state but live in the in-memory contact_capture.
            # Merge instead — only update fields that are non-None in the new capture.
            _new_contact = contact_capture.contact.model_dump(mode="json")
            _existing_ci = dict(state.contact_info or {})
            for _field in ("name", "email", "phone", "source", "confidence"):
                if _new_contact.get(_field) is not None:
                    _existing_ci[_field] = _new_contact[_field]
            # BUG-6040: a sign-off name ("...\nGonzalo Garcia\ngargonz@gmail.com") is
            # captured only by the LLM extractor (→ personal.name), not the deterministic
            # bare-block capture. The lead / consultation / CSR-sync path reads contact_info,
            # so back-fill it from personal.* — otherwise the bot greets "Gonzalo" but the
            # lead and CSR record never receive the name.
            _backfilled_contact = False
            for _pf in ("name", "email", "phone"):
                if not _existing_ci.get(_pf):
                    _pv = getattr(getattr(state.personal, _pf, None), "value", None)
                    if is_real_contact_value(_pv):
                        _existing_ci[_pf] = _pv
                        _backfilled_contact = True
            state.contact_info = _existing_ci
            # Re-merge so contact readiness reflects the back-filled personal.* values
            # THIS turn. Without this, a sign-off / digit-bearing name only the LLM caught
            # (e.g. "Gonzalo Garcia", "E2E Smoke Test") would leave lead_contact_ready False
            # until the next turn — stalling lead creation and the consultation flow.
            if _backfilled_contact:
                contact_capture = self.contact_capture_detector.merge_with_state(
                    contact_capture, state
                )
            # Phase 4 hotfix: sync contact_capture into personal + lead state so that
            # contact_slots() can find the contact on all subsequent turns.
            _sync_contact_capture_to_state(state, contact_capture)
            STATE_UPDATES.labels(result="applied").inc()
            event_id, event_sequence, previous_event_hash = await self._append_thread_event(
                thread_id=thread_id,
                sequence=event_sequence,
                previous_hash=previous_event_hash,
                event_type="extraction.applied",
                payload={
                    "delta_count": len(extraction.state_deltas),
                    "llm_delta_count": _llm_extraction_delta_count,
                },
                _buf=_evt_buf,
            )
            event_ids.append(event_id)
            action_plan = self.action_planner.plan(
                processed=processed,
                state=state,
                intent=intent,
                extraction=extraction,
            )
            # When the planner has already produced a CONFIRMED pending action (the user
            # said "yes" to a pending booking/quote/etc.), that ready-to-execute plan must
            # not be clobbered by the lead-objective override or the consultation reconcile
            # below — otherwise the confirmation is lost and the action never dispatches
            # (BUG-6040: "yes" stalled at ready_to_schedule and never booked).
            _is_confirmed_pending = bool(
                action_plan.status == ActionStatus.READY
                and getattr(action_plan, "pending_confirmation_key", None)
                and isinstance(action_plan.collected_slots, dict)
                and action_plan.collected_slots.get("confirmed")
            )
            lead_objective_decision = self.lead_objective_engine.decide(
                message=payload.message,
                intent=intent,
                state=state,
                attachment_intake=attachment_intake_result,
                contact_capture=contact_capture,
                turn_count=thread.turn_count,
            )
            state.lead_objective_stage = lead_objective_decision.stage
            # PR 4: service metadata extraction.
            _active_svc = (
                state.project.services_discussed[-1].service.value
                if state.project.services_discussed
                else None
            )
            metadata_result = self.service_metadata_extractor.extract(
                payload.message,
                active_service=_active_svc,
                existing_confirmed=state.service_metadata or {},
                existing_candidates=state.metadata_candidates or {},
            )
            # Apply confirmed platforms/formats/isbn to state (safe merge — no overwrite).
            if metadata_result.publishing_platforms:
                _existing_pp = set(state.publishing_platforms or [])
                for _p in metadata_result.publishing_platforms:
                    if _p not in _existing_pp:
                        state.publishing_platforms = list(state.publishing_platforms) + [_p]
            if metadata_result.book_formats:
                _existing_bf = set(state.project.book_formats or [])
                for _f in metadata_result.book_formats:
                    if _f not in _existing_bf:
                        state.project.book_formats = list(state.project.book_formats) + [_f]
            if metadata_result.isbn_status and not state.isbn_status:
                state.isbn_status = metadata_result.isbn_status
            for _svc_key, _svc_meta in metadata_result.confirmed.items():
                if _svc_key not in state.service_metadata:
                    state.service_metadata[_svc_key] = {}
                for _mk, _mv in _svc_meta.items():
                    if _mk not in state.service_metadata[_svc_key]:
                        state.service_metadata[_svc_key][_mk] = _mv
            for _svc_key, _cands in metadata_result.candidates.items():
                if _svc_key not in state.metadata_candidates:
                    state.metadata_candidates[_svc_key] = []
                state.metadata_candidates[_svc_key].extend(_cands)
            # PR 3: attachment assessment priority.
            attachment_priority_decision = self.attachment_assessment_priority.decide(
                attachment_intake_result,
                contact_ready=contact_capture.lead_contact_ready,
            )
            # PR 2: consultation-first sales planner.
            current_question_priority = self.current_question_priority_detector.detect(
                payload.message
            )
            answer_before_capture_decision = self.answer_before_capture_policy.decide(
                priority=current_question_priority,
                contact_ready=contact_capture.lead_contact_ready,
            )
            # Capture the prior-turn consultation stage BEFORE any engine overwrites it
            # this turn, so the once-only phone ask (reduce_consultation_state) is
            # loop-safe across turns and the two intra-turn reducer passes.
            _prior_consultation_stage = getattr(state, "consultation_stage", None)
            consultation_objective_decision = self.consultation_objective_engine.decide(
                message=payload.message,
                state=state,
                lead_objective_decision=lead_objective_decision,
                contact_capture=contact_capture,
                current_question_priority=current_question_priority,
            )
            # Apply extracted preferred_call_time to state if found this turn.
            if consultation_objective_decision.extracted_preferred_call_time:
                state.preferred_call_time = (
                    consultation_objective_decision.extracted_preferred_call_time
                )
            # Track current question type and answer-before-capture flag in state.
            if current_question_priority.has_priority:
                state.current_question_type = current_question_priority.question_type
                state.answer_before_capture_applied = (
                    answer_before_capture_decision.suppress_contact_until_answered
                )
            # Sync consultation_stage from decision.
            if consultation_objective_decision.stage:
                state.consultation_stage = consultation_objective_decision.stage

            # Contact-recovery signals: detect "already shared" / complaint / PII misuse.
            _already_shared_signal = user_claims_already_shared(payload.message)
            _complaint_signal = user_has_complaint_or_privacy_concern(payload.message)
            _pii_misuse_signal = user_objects_to_pii_misuse(payload.message)
            _contact_info_ready = contact_is_ready(state.contact_info or {})
            # Batch 4: structured complaint classification.
            complaint_classification = self.complaint_classifier.classify(payload.message)
            # Persona: evaluate identity question and persist name to state.
            persona_decision = self.persona.evaluate(
                message=payload.message, state=state
            )
            # Persist chosen name into state so it survives across turns.
            if persona_decision.representative_name and not state.representative_name:
                state.representative_name = persona_decision.representative_name
            # Context enforcement gate — converts all signals into an enforceable decision.
            context_enforcement_decision = self.context_enforcement_gate.enforce(
                text=payload.message,
                intent=intent,
                state=state,
                processed=processed,
                context_pack=None,  # pack not yet built; enforcement uses state directly
                current_question_priority=current_question_priority,
                consultation_objective=consultation_objective_decision,
                service_metadata_extraction=metadata_result,
                negation_targets=processed.negation_targets or None,
                delegated_decision=self.slot_tracker.last_decision,
            )
            # Apply safe state updates from enforcement decision.
            if context_enforcement_decision.state_updates:
                upd = context_enforcement_decision.state_updates
                if upd.get("clear_genre"):
                    state.project.genre = state.project.genre.model_copy(update={"value": None})
                    state.project.genre_status = "uncertain"
                    if upd.get("genre_candidate"):
                        cands = list(state.project.genre_candidates or [])
                        cand = str(upd["genre_candidate"])
                        if cand not in cands:
                            cands.append(cand)
                        state.project.genre_candidates = cands
                if "publishing_platforms" in upd:
                    state.publishing_platforms = list(upd["publishing_platforms"])
                if "book_formats" in upd:
                    state.project.book_formats = list(upd["book_formats"])
                # Clear wrongly inferred "published" status when user explicitly corrects.
                if upd.get("clear_manuscript_status"):
                    state.project.manuscript_status = state.project.manuscript_status.model_copy(
                        update={"value": None}
                    )
            state.lead_intake_payload = self._build_lead_intake_payload(
                state=state,
                message=payload.message,
                intent=intent,
                attachment_intake=attachment_intake_result,
                contact_capture=contact_capture,
                thread_id=thread_id,
                customer_id=payload.customer_id,
            ).model_dump(mode="json")
            if (
                lead_objective_decision.objective_move == "create_lead"
                and not state.lead_created
                and contact_capture.lead_contact_ready
                and not _is_confirmed_pending
            ):
                action_plan = self._create_lead_action_plan_from_contact(
                    state=state,
                    contact_capture=contact_capture,
                    intent=intent,
                )
            # Phase 6 hotfix: run canonical consultation state reducer BEFORE applying plan.
            # This overrides the action_planner when all details are now present in state
            # (contact_slots Phase 3 fix ensures contact is found from state.contact_info).
            consultation_state_decision = reduce_consultation_state(
                state=state,
                message=payload.message,
                intent=intent,
                contact_ready=contact_capture.lead_contact_ready,
                action_plan=action_plan,
                action_result=None,
                has_email=contact_capture.has_email,
                has_phone=contact_capture.has_phone,
                require_phone=self.consultation_require_phone,
                prior_stage=_prior_consultation_stage,
            )
            if consultation_state_decision.can_schedule and not _is_confirmed_pending:
                action_plan = _reconcile_consultation_action_plan(
                    current_plan=action_plan,
                    consultation_decision=consultation_state_decision,
                    state=state,
                    contact_capture=contact_capture,
                )
            # Canonical reducer stage overrides the objective engine's earlier sync.
            state.consultation_stage = str(consultation_state_decision.stage)
            self._apply_sales_action_plan_to_state(state, action_plan)
            governance_decision = self.tool_governance_gate.evaluate(
                action_plan=action_plan,
                intent=intent,
                processed=processed,
                state=state,
                thread_id=thread_id,
            )
            if governance_decision.allowed:
                action_result = await self.action_dispatcher.dispatch(
                    action_plan,
                    thread_id=thread_id,
                    customer_id=payload.customer_id,
                )
            else:
                action_result = None
            self._apply_sales_action_result_to_state(state, action_result)
            # Phase 6 hotfix: re-run reducer with actual action_result for final stage.
            consultation_state_decision = reduce_consultation_state(
                state=state,
                message=payload.message,
                intent=intent,
                contact_ready=contact_capture.lead_contact_ready,
                action_plan=action_plan,
                action_result=action_result,
                has_email=contact_capture.has_email,
                has_phone=contact_capture.has_phone,
                require_phone=self.consultation_require_phone,
                prior_stage=_prior_consultation_stage,
            )
            state.consultation_stage = str(consultation_state_decision.stage)
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
                _buf=_evt_buf,
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
            # Phase 12 PR 5: portfolio fallback policy.
            portfolio_fallback_decision: PortfolioFallbackDecision | None = (
                self.portfolio_fallback_policy.decide(
                    message=payload.message,
                    intent=intent,
                    state=state,
                    action_plan=action_plan,
                )
            )
            if portfolio_fallback_decision is not None:
                update_portfolio_filter_state(
                    state,
                    decision=portfolio_fallback_decision,
                    turn_id=str(event_id),
                )

            portfolio_response: PortfolioResponse | None = self._portfolio_response_from_action(
                action_result
            )
            # Portfolio engine: only fire when the intent is a high-confidence, genuine
            # portfolio request.  "print a sample" / "sample copy" means the author wants
            # a proof of their OWN book — that is NOT a portfolio request.
            _portfolio_request_genuine = (
                intent.query_primary == QueryIntentType.PORTFOLIO_REQUEST
                and intent.confidence >= 0.85
                and not _is_proof_copy_request(payload.message)
            )
            if (
                portfolio_response is None
                and _portfolio_request_genuine
                and portfolio_fallback_decision is not None
                and portfolio_fallback_decision.strategy != "ask_filter_once"
            ):
                portfolio_response = await self._portfolio_turn(
                    thread_id=thread_id,
                    customer_id=payload.customer_id,
                    turn_sequence=event_sequence + 1,
                    correlation_id=payload.correlation_id,
                    state=state,
                    intent_service=intent.service_primary,
                    message=payload.message,
                    fallback_decision=portfolio_fallback_decision,
                )
            elif (
                portfolio_response is None
                and _portfolio_request_genuine
                and portfolio_fallback_decision is None
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
            if _llm_extraction_task is not None:
                try:
                    _llm_result = await _llm_extraction_task
                    if _llm_result.state_deltas:
                        _llm_extraction_delta_count = len(_llm_result.state_deltas)
                        _llm_combined = CombinedExtraction(state_deltas=_llm_result.state_deltas)
                        state = self.state_applier.apply(state, _llm_combined, rejected_paths=_rejected_delta_paths)
                        extraction.state_deltas.extend(_llm_result.state_deltas)
                    if _llm_result.rich_metadata:
                        if "book_specs" not in state.service_metadata:
                            state.service_metadata["book_specs"] = {}
                        for _k, _v in _llm_result.rich_metadata.items():
                            if _k not in state.service_metadata["book_specs"]:
                                state.service_metadata["book_specs"][_k] = _v
                    if self.entity_index is not None:
                        for _delta in _llm_result.state_deltas:
                            try:
                                await self.entity_index.upsert_structured_fact(
                                    thread_id=thread_id,
                                    entity_path=_delta.path,
                                    entity_value=str(_delta.value),
                                    confidence=_delta.confidence,
                                    source_extraction=True,
                                    turn_index=event_sequence,
                                )
                            except Exception:
                                ENTITY_INDEX_FAILURES.labels(kind="structured_fact").inc()
                        for _rk, _rv in _llm_result.rich_metadata.items():
                            try:
                                await self.entity_index.upsert_free_text_fact(
                                    thread_id=thread_id, key=_rk, value=_rv, confidence=0.9, turn_index=event_sequence,
                                )
                            except Exception:
                                ENTITY_INDEX_FAILURES.labels(kind="free_text_fact").inc()
                except Exception as _llm_exc:
                    structlog.get_logger(__name__).warning(
                        "llm_metadata_extraction_failed",
                        thread_id=str(thread_id),
                        exception_class=_llm_exc.__class__.__name__,
                    )
            with STAGE_LATENCY.labels(stage="context_build").time():
                trg_context, _preloaded_trg_graph = await self._build_trg_context(thread_id)
                trg_response_hint = _trg_response_hint_from_context(
                    state=state,
                    intent=intent,
                    trg_context=trg_context,
                )

                # Retrieve semantically relevant entity memory and build a context delta hint.
                # Only inject facts that are NOT already present in the current thread state.
                _entity_hint: str | None = None
                if self.entity_index is not None:
                    try:
                        _entity_docs = await self.entity_index.retrieve_relevant(
                            thread_id=thread_id,
                            query_text=payload.message,
                            top_k=6,
                        )
                        _entity_hint = _build_entity_context_hint(_entity_docs, state)
                    except Exception as _ei_exc:  # noqa: BLE001
                        structlog.get_logger(__name__).debug(
                            "entity_retrieval_skipped",
                            thread_id=str(thread_id),
                            exception_class=_ei_exc.__class__.__name__,
                        )

                project_snapshot: ProjectContextSnapshot = self.project_context_manager.decide(
                    message=payload.message,
                    state=state,
                    intent=intent,
                )
                # Persist multi-project state so it survives across turns.
                state.conversation_projects = [
                    p.model_dump(mode="json") for p in project_snapshot.projects
                ]
                context_pack = self.context_pack_builder.build(
                    state=state,
                    intent=intent,
                    runtime_atoms=processed.deterministic_atoms,
                    trg_context=trg_context,
                    project_snapshot=project_snapshot,
                    context_enforcement=context_enforcement_decision,
                )
            response_hint = context_pack.response_hint or trg_response_hint
            if _entity_hint:
                response_hint = f"{response_hint}\n{_entity_hint}" if response_hint else _entity_hint

            # CSR context injection: if a CSR conversation has been ingested, give the bot
            # the abstract + recent verbatim turns + commitment list as read-only context.
            _csr_hint = _build_csr_context_hint(state)
            if _csr_hint:
                response_hint = f"{response_hint}\n{_csr_hint}" if response_hint else _csr_hint

            # Service workflow: inject rich sequencing advice into the LLM prompt.
            # Patterns handled:
            #   a) Sequencing question ("what comes next?", "can I do both at once?")
            #      → full pipeline or multi-service advice
            #   b) Single active service → predecessor/successor/parallel facts
            #   c) Multi-service message → topological order + parallel wins
            _wf_hint = ""
            _mentioned_services = resolve_service_aliases(payload.message)
            _active_svc_raw = (
                state.project.services_discussed[-1].service.value
                if state.project.services_discussed
                else None
            )
            _active_svc_str: str | None = str(_active_svc_raw) if _active_svc_raw else None

            if is_sequencing_question(payload.message):
                # User asked about order/parallel — give full or multi-service advice.
                _services_to_advise = _mentioned_services or (
                    [_active_svc_str] if _active_svc_str else []
                )
                if len(_services_to_advise) >= 2:
                    _multi = self.service_workflow.advise_multi(_services_to_advise)
                    _wf_hint = _multi.as_prompt_facts()
                elif _services_to_advise:
                    _wf_hint = self.service_workflow.user_guidance(_services_to_advise[0])
                else:
                    _wf_hint = self.service_workflow.full_pipeline_text()
            elif len(_mentioned_services) >= 2:
                # User mentioned multiple services — show the sequence.
                _multi = self.service_workflow.advise_multi(_mentioned_services)
                _wf_hint = _multi.as_prompt_facts()
            elif _active_svc_str:
                # Single active service — show prerequisites, parallel, next steps.
                _wf_hint = self.service_workflow.user_guidance(_active_svc_str)

            if _wf_hint:
                response_hint = f"{response_hint}\n{_wf_hint}" if response_hint else _wf_hint

            response_plan = self.response_planner.plan(
                intent=intent,
                state=state,
                context_pack=context_pack,
                tool_governance=governance_decision,
                action_plan=action_plan,
                action_result=action_result,
                negation_targets=processed.negation_targets or None,
                portfolio_fallback_decision=portfolio_fallback_decision,
                lead_objective_decision=lead_objective_decision,
                contact_capture_result=contact_capture,
                consultation_objective_decision=consultation_objective_decision,
                current_question_priority=current_question_priority,
                answer_before_capture_decision=answer_before_capture_decision,
                attachment_priority_decision=attachment_priority_decision,
                context_enforcement=context_enforcement_decision,
                complaint_classification=complaint_classification,
            )

            # Phase 12 PR 4: detect slot delegation/declination and rebuild if needed.
            slot_statuses = self.slot_tracker.update(
                text=payload.message,
                state=state,
                response_plan_next_question=response_plan.next_question,
                context_pack=context_pack,
                turn_id=str(event_id),
            )
            if slot_statuses:
                state.slot_resolution_statuses = [s.model_dump(mode="json") for s in slot_statuses]
                context_pack = self.context_pack_builder.build(
                    state=state,
                    intent=intent,
                    runtime_atoms=processed.deterministic_atoms,
                    trg_context=trg_context,
                    project_snapshot=project_snapshot,
                    context_enforcement=context_enforcement_decision,
                )
                response_hint = context_pack.response_hint or trg_response_hint
                response_plan = self.response_planner.plan(
                    intent=intent,
                    state=state,
                    context_pack=context_pack,
                    tool_governance=governance_decision,
                    action_plan=action_plan,
                    action_result=action_result,
                    negation_targets=processed.negation_targets or None,
                    portfolio_fallback_decision=portfolio_fallback_decision,
                    lead_objective_decision=lead_objective_decision,
                    contact_capture_result=contact_capture,
                    consultation_objective_decision=consultation_objective_decision,
                    current_question_priority=current_question_priority,
                    answer_before_capture_decision=answer_before_capture_decision,
                    context_enforcement=context_enforcement_decision,
                    complaint_classification=complaint_classification,
                )

            # Phase 12 PR 6: flexible intent routing.
            flexible_intent_decision: FlexibleIntentDecision = self.flexible_intent_router.route(
                text=payload.message,
                intent=intent,
                state=state,
                context_pack=context_pack,
                response_plan=response_plan,
                delegated_decision=self.slot_tracker.last_decision,
                portfolio_fallback_decision=portfolio_fallback_decision,
            )
            if flexible_intent_decision.detected:
                response_plan = self.response_planner.plan(
                    intent=intent,
                    state=state,
                    context_pack=context_pack,
                    tool_governance=governance_decision,
                    action_plan=action_plan,
                    action_result=action_result,
                    negation_targets=processed.negation_targets or None,
                    portfolio_fallback_decision=portfolio_fallback_decision,
                    flexible_intent_decision=flexible_intent_decision,
                    lead_objective_decision=lead_objective_decision,
                    contact_capture_result=contact_capture,
                    consultation_objective_decision=consultation_objective_decision,
                    current_question_priority=current_question_priority,
                    answer_before_capture_decision=answer_before_capture_decision,
                    context_enforcement=context_enforcement_decision,
                    complaint_classification=complaint_classification,
                )

            # Phase 8 hotfix: override response plan for consultation status questions.
            # When the user asks "have my consultation been scheduled?", the planner
            # may not know to answer from state — override primary_goal so the generator
            # produces a status answer rather than re-asking for contact details.
            if consultation_state_decision.is_status_question:
                if consultation_state_decision.stage == ConsultationStage.SCHEDULED:
                    response_plan = response_plan.model_copy(
                        update={
                            "primary_goal": "consultation_status_scheduled",
                            "next_question": None,
                        }
                    )
                elif consultation_state_decision.stage == ConsultationStage.PENDING_CONFIRMATION:
                    response_plan = response_plan.model_copy(
                        update={"primary_goal": "consultation_status_pending"}
                    )
                elif consultation_state_decision.consultation_requested:
                    response_plan = response_plan.model_copy(
                        update={"primary_goal": "consultation_status_in_progress"}
                    )

            # Phase 9: context-aware RAG retrieval using enriched query.
            rag_query = self.rag_query_builder.build(
                message=payload.message,
                intent=intent,
                context_pack=context_pack,
                response_plan=response_plan,
            )
            rag_chunks: list[RetrievedChunk] = []
            rag_status: str = "skipped"  # Batch 3 Step 16: trace RAG status
            if self.rag_retriever is not None and _allow_rag_for_intent(intent):
                # Enrich the processed message with the context-aware query text so
                # BM25 retrieval benefits from known service/genre/status context
                # while vector retrieval keeps the original embedding.
                processed_for_rag = processed.model_copy(
                    update={"normalized": rag_query.query_text}
                )
                try:
                    rag_chunks = await self.rag_retriever.retrieve(processed_for_rag, intent)
                    rag_status = "success" if rag_chunks else "empty"
                except Exception as exc:
                    rag_status = "failed"
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
                        _buf=_evt_buf,
                    )
                    event_ids.append(event_id)
            document_status_message: str | None
            if not governance_decision.allowed and governance_decision.blocked_message:
                document_status_message = governance_decision.blocked_message
            else:
                document_status_message = self._sales_action_status_message(
                    action_plan, action_result
                ) or _document_status_message(intent)
            # Step 2 (tone fix): build recent conversation turns from persisted state.
            _recent_turns: list[tuple[str, str]] | None = None
            if state.last_user_message and state.last_assistant_text:
                _recent_turns = [(state.last_user_message, state.last_assistant_text)]
            with STAGE_LATENCY.labels(stage="response_generation").time():
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
                    response_hint=response_hint,
                    context_pack=context_pack,
                    response_plan=response_plan,
                    recent_turns=_recent_turns,
                    persona_decision=persona_decision,
                )
            quality_report = self.response_quality_gate.evaluate(
                text=draft.text,
                intent=intent,
                state=state,
                context_pack=context_pack,
                response_plan=response_plan,
                tool_governance=governance_decision,
            )
            contract = CustomerResponseContract()
            production_like = self.environment not in {"test", "dev", "development", "local"}
            repair_attempted = False
            deterministic_final_text_blocked = False
            response_repair_quality = None
            final_draft = draft

            def _dev_fallback() -> ResponseDraft:
                if quality_report.safe_fallback is not None:
                    fallback_source = f"{draft.source}_quality_fallback"
                    if contract.is_allowed_final_source(
                        fallback_source,
                        app_env=self.environment,
                    ):
                        return ResponseDraft(
                            text=quality_report.safe_fallback,
                            source=fallback_source,
                        )
                return draft

            fallback_source = f"{draft.source}_quality_fallback"
            fallback_draft: ResponseDraft | None = None
            if quality_report.safe_fallback is not None and contract.is_allowed_final_source(
                fallback_source,
                app_env=self.environment,
            ):
                fallback_draft = ResponseDraft(
                    text=quality_report.safe_fallback,
                    source=fallback_source,
                )

            if (
                contract.is_allowed_final_source(draft.source, app_env=self.environment)
                and quality_report.passed
            ):
                final_draft = draft
            else:
                repair_draft: ResponseDraft | None = None
                if self.response_repair_enabled:
                    repair_attempted = True
                    repair_method = getattr(self.response_generator, "repair", None)
                    if callable(repair_method):
                        repair_draft = await repair_method(
                            bad_text=draft.text,
                            quality_report=quality_report,
                            response_plan=response_plan,
                            context_pack=context_pack,
                            tool_governance=governance_decision,
                            response_hint=response_hint,
                        )
                        response_repair_quality = self.response_quality_gate.evaluate(
                            text=repair_draft.text,
                            intent=intent,
                            state=state,
                            context_pack=context_pack,
                            response_plan=response_plan,
                            tool_governance=governance_decision,
                        )
                _claude_source = contract.is_allowed_final_source(
                    draft.source, app_env=self.environment
                )

                if (
                    repair_draft is not None
                    and response_repair_quality is not None
                    and response_repair_quality.passed
                    and contract.is_allowed_final_source(
                        repair_draft.source,
                        app_env=self.environment,
                    )
                ):
                    # Repair passed — use it.
                    final_draft = repair_draft
                elif (
                    fallback_draft is not None
                    and draft.source not in contract.allowed_final_sources
                ):
                    # Source not allowed (template) — use quality gate fallback.
                    final_draft = fallback_draft
                elif _claude_source:
                    # Claude responded — always prefer it over any hardcoded string.
                    final_draft = draft
                else:
                    # Non-Claude source and no repair/fallback available.
                    final_draft = _dev_fallback()

            final_text = final_draft.text
            final_source = final_draft.source
            sales_tone_report = quality_report.sales_tone
            if sales_tone_report is None or final_text != draft.text:
                sales_tone_report = self.response_style_policy.evaluate(
                    text=final_text,
                    response_plan=response_plan,
                    context_pack=context_pack,
                )
            # Step 8 (Batch 1): Use final_draft approved_urls, not original draft.
            # If repair/fallback changed the draft, the URLs must come from the
            # final version — not the original potentially-blocked draft.
            bubbles = self.formatter.format(
                final_text, approved_urls=set(final_draft.approved_urls)
            )
            # Batch 3 Step 3: only show lead form if we should be asking for contact now
            # (answer-before-capture must not be suppressing contact, and user must not be
            #  in complaint/non-lead context).
            _abc_suppresses = answer_before_capture_decision is not None and getattr(
                answer_before_capture_decision, "suppress_contact_until_answered", False
            )
            if (
                lead_objective_decision.objective_move == "ask_contact"
                and not contact_capture.lead_contact_ready
                and not _abc_suppresses
                and bubbles
            ):
                bubbles[0].rich_segments.append(
                    {
                        "type": "lead_intake_form",
                        "fields": ["name", "email", "phone", "message", "attachments"],
                        "required": ["name", "email_or_phone"],
                    }
                )
            # PR 3: inject portfolio rich link segments so URLs are clickable, not raw text.
            # Guard: only inject when the intent was genuinely a portfolio request with
            # sufficient confidence AND the response plan confirms portfolio matching.
            # This prevents sample links appearing when the user said "print a sample"
            # (meaning a proof copy of their own book, not BookCraft portfolio samples).
            # Inject portfolio rich segments when: intent was genuine AND engine found samples.
            # No goal restriction — if the engine found real samples and intent was high-confidence,
            # always show them (the planner goal can vary depending on prior conversation state).
            _portfolio_intent_genuine = (
                intent.query_primary == QueryIntentType.PORTFOLIO_REQUEST
                and intent.confidence >= 0.85
                and not _is_proof_copy_request(payload.message)
            )
            from bookcraft.components.portfolio.schemas import PortfolioStatus as _PortfolioStatus
            _portfolio_found = (
                portfolio_response is not None
                and portfolio_response.status == _PortfolioStatus.FOUND
            )
            if _portfolio_found and bubbles and _portfolio_intent_genuine:
                _port_segs = self.portfolio_rich_segment_builder.build(portfolio_response)
                if _port_segs:
                    bubbles[0].rich_segments.extend(_port_segs)
            trg_context_for_trace: TRGContext | None = trg_context
            if self.trg_background_persist_enabled:
                trg_context_for_trace = None
                asyncio.create_task(self._bg_trg_update_and_persist(
                    thread_id=thread_id,
                    turn_sequence=event_sequence + 1,
                    user_text=payload.message,
                    assistant_text=final_text,
                    previous_state=previous_state,
                    state_deltas=list(extraction.state_deltas),
                    arbiter_signals=arbiter_result.corrections + arbiter_result.audit,
                    preloaded_graph=_preloaded_trg_graph,
                ))
            elif self.trg_engine is not None:
                try:
                    with STAGE_LATENCY.labels(stage="trg_update").time():
                        trg_result = await self.trg_engine.update_after_turn(
                            thread_id=thread_id,
                            turn_sequence=event_sequence + 1,
                            user_text=payload.message,
                            assistant_text=final_text,
                            previous_state=previous_state,
                            state_deltas=extraction.state_deltas,
                            arbiter_signals=arbiter_result.corrections + arbiter_result.audit,
                            preloaded_graph=_preloaded_trg_graph,
                        )
                    # Rebuild context from the updated graph so trg_semantic reflects
                    # facts, service shifts, and contradictions recorded this turn.
                    trg_context_for_trace = self.trg_engine.build_context(trg_result.graph)
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
                        _buf=_evt_buf,
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
                        _buf=_evt_buf,
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
                    "source": final_source,
                },
                _buf=_evt_buf,
            )
            event_ids.append(event_id)
            # Batch 3 Step 4: mark lead confirmation as acknowledged so future turns
            # can resume normal discovery instead of looping on confirmation.
            if response_plan.primary_goal == "lead_created_confirmation":
                state.lead_created_acknowledged = True
            # Step 3 (tone fix): record whether this turn asked for contact,
            # so LeadObjectiveEngine can back off on the next turn if deflected.
            state.last_turn_asked_contact = (
                lead_objective_decision.objective_move == "ask_contact"
                or response_plan.primary_goal in {"lead_contact_capture", "consultation_handoff"}
            )
            # Mark that we asked for the second contact method so we never ask again.
            if lead_objective_decision.objective_move == "request_second_contact_method":
                state.contact_second_method_requested = True
            # Step 2 (tone fix): persist prior turn for conversation history in next turn.
            # Store normalized message (not raw) to avoid PII in state.
            state.last_user_message = (processed.normalized or "")[:300]
            state.last_assistant_text = final_text[:300]
            if self.thread_repository is not None:
                _save_attempts = 0
                while True:
                    try:
                        with STAGE_LATENCY.labels(stage="state_persist").time():
                            await self.thread_repository.save_state(
                                thread_id=thread_id,
                                state=state,
                                expected_version=thread.version,
                                language=language.language,
                            )
                        break
                    except ThreadVersionConflictError:
                        _save_attempts += 1
                        if _save_attempts >= 3:
                            structlog.get_logger(__name__).warning(
                                "thread_version_conflict_save_skipped",
                                thread_id=str(thread_id),
                                attempts=_save_attempts,
                            )
                            break
                        # Reload thread to get latest version, then retry save.
                        _fresh = await self.thread_repository.load_or_create(
                            thread_id=thread_id, customer_id=None
                        )
                        thread = _fresh
            else:
                self.threads[thread_id].state = state

            # Persist TRG facts to PostgreSQL so cold-start reload works after Redis TTL.
            if not self.trg_background_persist_enabled and self.trg_fact_store is not None and self.trg_engine is not None:
                try:
                    _pg_graph = await self.trg_engine.repository.load(thread_id)
                    if _pg_graph is not None and _pg_graph.semantic_facts:
                        await self.trg_fact_store.upsert_facts(
                            thread_id=thread_id,
                            facts=_pg_graph.semantic_facts,
                            turn_index=event_sequence,
                        )
                except Exception as _tfs_exc:  # noqa: BLE001
                    structlog.get_logger(__name__).warning(
                        "trg_fact_store_persist_failed",
                        thread_id=str(thread_id),
                        exception_class=_tfs_exc.__class__.__name__,
                    )

            # Write milestone checkpoint if newly reached.
            if self.conversation_checkpointer is not None:
                try:
                    await self.conversation_checkpointer.maybe_checkpoint(
                        thread_id=thread_id,
                        state=state,
                        turn_index=event_sequence,
                    )
                except Exception as _cp_exc:  # noqa: BLE001
                    structlog.get_logger(__name__).warning(
                        "conversation_checkpoint_failed",
                        thread_id=str(thread_id),
                        exception_class=_cp_exc.__class__.__name__,
                    )

            if _evt_buf is not None and _evt_buf.events:
                try:
                    await self.thread_repository.append_events_batch(thread_id=thread_id, events=_evt_buf.events)
                except Exception as _buf_exc:
                    structlog.get_logger(__name__).warning(
                        "event_buffer_flush_failed",
                        thread_id=str(thread_id),
                        exception_class=_buf_exc.__class__.__name__,
                    )
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
                        "source": final_source,
                        "bubble_count": len(bubbles),
                        "preview": redact_text(final_text)[:500],
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
                    "negation_targets": [
                        t.model_dump(mode="json") for t in processed.negation_targets
                    ],
                    "slot_resolution": [s.model_dump(mode="json") for s in slot_statuses],
                    "attachment_intake": attachment_intake_result.model_dump(mode="json"),
                    # Batch 2 Step 6/9: rejected delta paths + attachment parse errors.
                    "rejected_delta_paths": _rejected_delta_paths,
                    "attachment_parse_error": _attachment_parse_error,
                    # Step 3 (Batch 1): trace-safe — no raw PII in contact_capture or lead_intake.
                    "contact_capture": safe_contact_capture(contact_capture),
                    "lead_objective": lead_objective_decision.model_dump(mode="json"),
                    "lead_intake": safe_lead_intake(state.lead_intake_payload),
                    # PR 2: consultation-first trace keys.
                    "current_question_priority": current_question_priority.model_dump(mode="json"),
                    "answer_before_capture": answer_before_capture_decision.model_dump(mode="json"),
                    "consultation_objective": consultation_objective_decision.model_dump(
                        mode="json"
                    ),
                    # Phase 6 hotfix: canonical consultation state reducer output.
                    "consultation_state": consultation_state_decision.model_dump(mode="json"),
                    # PR 3: attachment assessment priority trace key.
                    "attachment_priority": attachment_priority_decision.model_dump(mode="json"),
                    # PR 4: input safety + metadata extraction trace keys.
                    "input_safety": input_safety_decision.model_dump(mode="json"),
                    "service_metadata_extraction": metadata_result.model_dump(mode="json"),
                    # Context enforcement trace key.
                    "context_enforcement": context_enforcement_decision.model_dump(mode="json"),
                    # Contact recovery signals (no raw PII — boolean flags only).
                    "contact_recovery": {
                        "already_shared_signal": _already_shared_signal,
                        "complaint_signal": _complaint_signal,
                        "pii_misuse_signal": _pii_misuse_signal,
                        "contact_info_ready": _contact_info_ready,
                    },
                    # Batch 4: structured complaint classification.
                    "complaint_classification": complaint_classification.model_dump(mode="json"),
                    # Persona: representative name for this thread.
                    "persona": persona_decision.model_dump(mode="json"),
                    "lead_created": bool(state.lead_created),
                    "delegated_decision": self.slot_tracker.last_decision.model_dump(mode="json")
                    if self.slot_tracker.last_decision is not None
                    else None,
                    "portfolio_fallback": portfolio_fallback_decision.model_dump(mode="json")
                    if portfolio_fallback_decision is not None
                    else None,
                    "flexible_intent": flexible_intent_decision.model_dump(mode="json"),
                    "context_pack": context_pack.model_dump(mode="json"),
                    "project_context": project_snapshot.model_dump(mode="json"),
                    "context_arbiter": {
                        "corrections": arbiter_result.corrections,
                        "audit": arbiter_result.audit,
                        "intent_before": ensemble_intent.model_dump(mode="json"),
                        "intent_after": intent.model_dump(mode="json"),
                    },
                    "action_plan": action_trace_payload(action_plan, action_result),
                    "rag_query": rag_query.model_dump(mode="json"),
                    "rag_status": rag_status,  # Batch 3 Step 16
                    "tool_governance": governance_decision.model_dump(mode="json"),
                    "response_plan": response_plan.model_dump(mode="json"),
                    "response_quality": quality_report.model_dump(mode="json"),
                    "response_repair_quality": response_repair_quality.model_dump(mode="json")
                    if response_repair_quality is not None
                    else None,
                    "customer_response_contract": {
                        "final_responder": contract.final_responder,
                        "final_source": final_source,
                        "contract_passed": contract.is_allowed_final_source(
                            final_source,
                            app_env=self.environment,
                        ),
                        "production_contract_passed": contract.is_production_compliant_source(
                            final_source
                        ),
                        "dev_fallback_used": (
                            not production_like
                            and not contract.is_production_compliant_source(final_source)
                            and contract.is_allowed_final_source(
                                final_source, app_env=self.environment
                            )
                        ),
                        "repair_attempted": repair_attempted,
                        "repair_source": final_draft.source if repair_attempted else None,
                        "deterministic_final_text_blocked": deterministic_final_text_blocked,
                        # Step 1 (Batch 1): fail-closed trace fields.
                        "final_response_source": final_source,
                        "quality_blocked": deterministic_final_text_blocked,
                        "audit": [
                            f"contract:final_source={final_source}",
                            f"contract:allowed={
                                contract.is_allowed_final_source(
                                    final_source,
                                    app_env=self.environment,
                                )
                            }",
                            f"contract:production_compliant={
                                contract.is_production_compliant_source(final_source)
                            }",
                            f"contract:repair_attempted={repair_attempted}",
                            f"contract:production_like={production_like}",
                        ],
                    },
                    "sales_tone": sales_tone_report.model_dump(mode="json"),
                    "trg_semantic": {
                        "active_facts": [
                            f.model_dump(mode="json") for f in trg_context_for_trace.active_facts
                        ]
                        if trg_context_for_trace is not None
                        else [],
                        "answered_questions": [
                            q.model_dump(mode="json")
                            for q in trg_context_for_trace.answered_questions
                        ]
                        if trg_context_for_trace is not None
                        else [],
                        "forbidden_reasks": trg_context_for_trace.forbidden_reasks
                        if trg_context_for_trace is not None
                        else [],
                        "contradictions": [
                            c.model_dump(mode="json") for c in trg_context_for_trace.contradictions
                        ]
                        if trg_context_for_trace is not None
                        else [],
                        "service_shifts": [
                            s.model_dump(mode="json") for s in trg_context_for_trace.service_shifts
                        ]
                        if trg_context_for_trace is not None
                        else [],
                        "project_shifts": _build_project_shifts(project_snapshot),
                        "slot_resolutions": _build_slot_resolutions(
                            slot_statuses, project_snapshot
                        ),
                        "delegations": _build_delegations(
                            self.slot_tracker.last_decision, project_snapshot
                        ),
                    },
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
            base_action_events = self._build_action_events(action_result)

            # When the planner decides the customer should book a consultation
            # (consultation_scoping goal) and no contact info is captured yet,
            # emit a show_consultation_form event so the chat widget renders a
            # booking button. Claude's intent drives this — not backend heuristics.
            _show_form_event: list[dict[str, object]] = []
            if (
                response_plan is not None
                and response_plan.primary_goal == "consultation_scoping"
                and not getattr(state.personal.name, "value", None)
                and not getattr(state.personal.phone, "value", None)
                and not any(e.get("type") == "show_consultation_form" for e in base_action_events)
            ):
                _show_form_event = [{"type": "show_consultation_form"}]

            return ChatTurnResponse(
                thread_id=thread_id,
                bubbles=bubbles,
                intent=intent,
                language_status=language.language,
                debug_event_ids=self._debug_event_ids(event_ids),
                action_events=base_action_events + _show_form_event,
            )

    async def handle_greet(self, payload: Any) -> Any:
        """Generate a personalised proactive first message when the chat widget opens.

        ``payload`` is a ``ChatGreetRequest``.  Imported lazily to avoid a circular
        import at module load time (same pattern as ``handle_turn``).
        """
        from bookcraft.api.chat import ChatTurnResponse  # noqa: F401

        thread = await self._load_greet_thread(payload)
        thread_id = thread.thread_id

        if self.response_generator.adapter is not None:
            greeting_text = await _llm_proactive_greeting(
                adapter=self.response_generator.adapter,
                landing_page=payload.landing_page,
                landing_keyword=payload.landing_keyword,
            )
        else:
            greeting_text = _GREET_FALLBACK

        bubbles = self.formatter.format(greeting_text)
        self._record_live_trace(
            {
                "thread_id": str(thread_id),
                "customer_id": str(payload.customer_id) if payload.customer_id else None,
                "correlation_id": payload.correlation_id,
                "event": "proactive_greet",
                "landing_page": payload.landing_page,
                "landing_keyword": payload.landing_keyword,
                "greeting_preview": greeting_text[:200],
            }
        )
        return ChatTurnResponse(
            thread_id=thread_id,
            bubbles=bubbles,
            intent=None,
            language_status="en",
            debug_event_ids=[],
            action_events=[],
        )

    async def handle_csr_turn(self, payload: Any) -> None:
        """Ingest a CSR message for context — no bot response is generated.

        ``payload`` is a ``CsrTurnRequest``. The method:
        1. Detects and stores price/timeline commitments separately.
        2. Compresses the CSR message into the sliding-window abstract.
        3. Runs LLM metadata extraction on the *user* side of the conversation only.
        4. Persists updated state.
        """
        from bookcraft.api.chat import CsrTurnRequest as _CsrReq

        if not isinstance(payload, _CsrReq):
            return

        if self.thread_repository is not None:
            thread = await self.thread_repository.load_or_create(
                thread_id=payload.thread_id,
                customer_id=None,
            )
        else:
            memory = self.threads.setdefault(payload.thread_id, ThreadMemory())
            thread = LoadedThread(
                thread_id=payload.thread_id,
                state=memory.state,
                version=0,
                turn_count=0,
                event_count=0,
                last_event_hash=None,
            )

        state = thread.state

        # 1. Detect commitments (price/timeline) — kept separate from summary.
        new_commitments = detect_commitments(payload.csr_message)
        if new_commitments:
            state.csr_commitments = list(state.csr_commitments) + [
                {
                    "type": c.commitment_type,
                    "text": c.text,
                    "turn_index": state.csr_turns_ingested,
                    "csr_name": payload.csr_name,
                }
                for c in new_commitments
            ]

        # 2. Sliding-window summarization (commitment text excluded from abstract).
        await self.csr_summarizer.ingest(
            state=state,
            user_message=payload.user_message,
            csr_message=payload.csr_message,
            csr_name=payload.csr_name,
        )

        # 3. LLM extraction on user side only (CSR claims must not update state facts).
        if self.llm_metadata_extractor is not None and payload.user_message:
            try:
                _llm_result = await self.llm_metadata_extractor.extract(
                    user_text=payload.user_message,
                    assistant_text=payload.csr_message,
                    state=state,
                )
                if _llm_result.state_deltas:
                    from bookcraft.components.extraction.schemas import CombinedExtraction as _CE

                    state = self.state_applier.apply(state, _CE(state_deltas=_llm_result.state_deltas))
            except Exception as _exc:  # noqa: BLE001
                structlog.get_logger(__name__).warning(
                    "csr_turn_llm_extraction_failed",
                    thread_id=str(payload.thread_id),
                    exception_class=_exc.__class__.__name__,
                )

        state.csr_turns_ingested = state.csr_turns_ingested + 1

        # 4. Persist.
        if self.thread_repository is not None:
            await self.thread_repository.save_state(
                thread_id=thread.thread_id,
                state=state,
                expected_version=thread.version,
                language="en",
            )
        else:
            self.threads[thread.thread_id].state = state

    async def get_thread_debug_state(self, thread_id: Any) -> dict:
        """Return a structured debug snapshot of a thread's current state.

        Used by the CSR dashboard to show the AI chatbot's extracted facts,
        consultation status, and TRG context for a conversation.
        """
        from uuid import UUID as _UUID

        try:
            tid = thread_id if isinstance(thread_id, _UUID) else _UUID(str(thread_id))
        except Exception:
            return {"error": "invalid_thread_id", "thread_id": str(thread_id)}

        if self.thread_repository is not None:
            try:
                thread = await self.thread_repository.load_or_create(
                    thread_id=tid, customer_id=None
                )
                state = thread.state
                turn_count = thread.turn_count
            except Exception as exc:
                return {"error": str(exc), "thread_id": str(tid)}
        else:
            memory = self.threads.get(tid)
            if memory is None:
                return {"error": "thread_not_found", "thread_id": str(tid)}
            state = memory.state
            turn_count = 0

        def _fv(field_obj: object) -> dict | None:
            val = getattr(field_obj, "value", None)
            if val is None:
                return None
            return {
                "value": str(val),
                "confidence": getattr(field_obj, "confidence", None),
                "source": str(getattr(field_obj, "source", "")),
            }

        return {
            "thread_id": str(tid),
            "turn_count": turn_count,
            "personal": {
                "name": _fv(state.personal.name),
                "email": _fv(state.personal.email),
                "phone": _fv(state.personal.phone),
                "timezone": _fv(state.personal.timezone),
                "preferred_contact_method": _fv(state.personal.preferred_contact_method),
            },
            "project": {
                "title": _fv(state.project.title),
                "genre": _fv(state.project.genre),
                "word_count": _fv(state.project.word_count),
                "page_count": _fv(state.project.page_count),
                "manuscript_status": _fv(state.project.manuscript_status),
                "services_discussed": [
                    {
                        "service": str(getattr(getattr(s, "service", None), "value", s.service)),
                        "confidence": getattr(s, "confidence", None),
                    }
                    for s in (state.project.services_discussed or [])
                ],
            },
            "commercial": {
                "budget_range": _fv(state.commercial.budget_range),
                "timeline": _fv(state.commercial.timeline_expectation),
            },
            "consultation": {
                "stage": getattr(state, "consultation_stage", None),
                "preferred_call_time": getattr(state, "preferred_call_time", None),
                "handoff_created": getattr(state, "consultation_handoff_created", False),
            },
            "pending_confirmation": _pending_confirmation_debug(state),
            "sales_stage": str(getattr(getattr(state, "sales_stage", None), "value", None) or ""),
            "csr_commitments": list(getattr(state, "csr_commitments", None) or []),
            "service_metadata": dict(getattr(state, "service_metadata", None) or {}),
        }

    async def handle_inject_facts(self, payload: Any) -> Any:
        """Inject verified CRM facts (name/email/phone) into a thread's state.

        ``payload`` is a ``ChatFactsRequest``.  Each non-null field is applied
        as a StateDelta with confidence 0.98 (form-verified data).  Uses
        optimistic-locking retry (up to 3 attempts) so concurrent calls from
        handle_turn never cause a ThreadVersionConflictError to surface as a 500.

        Returns a ``ChatFactsResponse`` listing which fields were actually updated.
        """
        from bookcraft.api.chat import ChatFactsRequest as _FR, ChatFactsResponse as _FResp
        from bookcraft.components.extraction.schemas import CombinedExtraction, StateDelta
        from bookcraft.domain.enums import Source

        if not isinstance(payload, _FR):
            return _FResp(thread_id=payload.thread_id, fields_applied=[])

        # Build deltas FIRST (before any thread loading).
        # Confidence 0.98: fills gaps and corrects low-confidence guesses without
        # overwriting already-verified facts (StateApplier enforces this).
        deltas: list[StateDelta] = []
        for state_path, value in [
            ("personal.name",  payload.name),
            ("personal.email", payload.email),
            ("personal.phone", payload.phone),
        ]:
            if value and isinstance(value, str) and value.strip():
                deltas.append(StateDelta(
                    path=state_path,
                    value=value.strip(),
                    confidence=0.98,
                    source=Source.AI_EXTRACTED,
                    extracted_by=f"crm_sync.{payload.source_label}",
                    raw_excerpt=None,
                ))

        # Nothing to do — return immediately without touching the thread.
        if not deltas:
            return _FResp(thread_id=payload.thread_id, fields_applied=[])

        _log = structlog.get_logger(__name__)
        _label_map = {"personal.name": "name", "personal.email": "email", "personal.phone": "phone"}
        _MAX_RETRIES = 3

        for _attempt in range(_MAX_RETRIES):
            try:
                # Always reload fresh — this gets the latest version and avoids conflict.
                if self.thread_repository is not None:
                    _thread = await self.thread_repository.load_or_create(
                        thread_id=payload.thread_id, customer_id=None,
                    )
                    _state = self.state_applier.apply(
                        _thread.state, CombinedExtraction(state_deltas=deltas),
                    )
                    await self.thread_repository.save_state(
                        thread_id=_thread.thread_id,
                        state=_state,
                        expected_version=_thread.version,
                        language="en",
                    )
                else:
                    _mem = self.threads.setdefault(payload.thread_id, ThreadMemory())
                    _state = self.state_applier.apply(
                        _mem.state, CombinedExtraction(state_deltas=deltas),
                    )
                    self.threads[payload.thread_id].state = _state

                # Collect fields that were actually written.
                fields_applied = [
                    _label_map.get(d.path, d.path)
                    for d in deltas
                    if getattr(
                        getattr(_state, d.path.split(".")[0], None),
                        d.path.split(".")[1], None,
                    ) is not None
                    and getattr(
                        getattr(_state, d.path.split(".")[0], None),
                        d.path.split(".")[1],
                    ).value == d.value
                ]

                _log.info(
                    "crm_facts_injected",
                    thread_id=str(payload.thread_id),
                    source_label=payload.source_label,
                    fields_applied=fields_applied,
                    attempt=_attempt,
                )
                return _FResp(thread_id=payload.thread_id, fields_applied=fields_applied)

            except Exception as _exc:  # noqa: BLE001
                _is_conflict = (
                    "versionconflict" in type(_exc).__name__.lower()
                    or "version conflict" in str(_exc).lower()
                    or "conflict" in str(_exc).lower()
                )
                if _is_conflict and _attempt < _MAX_RETRIES - 1:
                    _log.warning("inject_facts_conflict_retry",
                                 thread_id=str(payload.thread_id), attempt=_attempt)
                    await asyncio.sleep(0.05 * (2 ** _attempt))
                    continue
                _log.warning("inject_facts_save_failed",
                             thread_id=str(payload.thread_id),
                             attempt=_attempt,
                             exception_class=type(_exc).__name__)
                return _FResp(thread_id=payload.thread_id, fields_applied=[])

        return _FResp(thread_id=payload.thread_id, fields_applied=[])

    async def handle_handover(self, payload: Any) -> Any:
        """Signal a handover between bot and CSR.

        ``payload`` is a ``HandoverRequest``.
        direction=to_csr: activate CSR handover mode.
        direction=to_bot: deactivate and write a permanent checkpoint.
        """
        from bookcraft.api.chat import HandoverRequest as _HR
        from bookcraft.api.chat import HandoverResponse as _HResp

        if not isinstance(payload, _HR):
            return

        if self.thread_repository is not None:
            thread = await self.thread_repository.load_or_create(
                thread_id=payload.thread_id,
                customer_id=None,
            )
        else:
            memory = self.threads.setdefault(payload.thread_id, ThreadMemory())
            thread = LoadedThread(
                thread_id=payload.thread_id,
                state=memory.state,
                version=0,
                turn_count=0,
                event_count=0,
                last_event_hash=None,
            )

        state = thread.state

        if payload.direction == "to_csr":
            state.csr_handover_active = True
            if payload.csr_id:
                state.sales_actions.consultation.csr_id = payload.csr_id
            if payload.csr_name:
                state.sales_actions.consultation.csr_name = payload.csr_name

        elif payload.direction == "to_bot":
            state.csr_handover_active = False
            state.csr_handover_returned_at = datetime.now(UTC)
            # Write permanent checkpoint so bot retains all CSR-gathered context.
            if self.conversation_checkpointer is not None:
                try:
                    await self.conversation_checkpointer.force_checkpoint(
                        thread_id=thread.thread_id,
                        state=state,
                        milestone="csr_handover_returned",
                    )
                except Exception as _cp_exc:  # noqa: BLE001
                    structlog.get_logger(__name__).warning(
                        "handover_checkpoint_failed",
                        thread_id=str(payload.thread_id),
                        exception_class=_cp_exc.__class__.__name__,
                    )

        if self.thread_repository is not None:
            await self.thread_repository.save_state(
                thread_id=thread.thread_id,
                state=state,
                expected_version=thread.version,
                language="en",
            )
        else:
            self.threads[thread.thread_id].state = state

        return _HResp(
            thread_id=thread.thread_id,
            direction=payload.direction,
            csr_handover_active=state.csr_handover_active,
        )

    async def _load_greet_thread(self, payload: Any) -> LoadedThread:
        """Load or create a thread for the proactive greet flow."""
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

    @staticmethod
    def _apply_sales_action_plan_to_state(
        state: ThreadState,
        action_plan: ActionPlan,
    ) -> None:
        if action_plan.confirmation_required and action_plan.pending_confirmation_key:
            state.sales_actions.pending_confirmation.type = action_plan.pending_confirmation_key
            state.sales_actions.pending_confirmation.payload = action_plan.collected_slots
            state.sales_actions.pending_confirmation.created_at = datetime.now(UTC)
            # Phase 3 Batch 4: copy planner-computed expiry into state so it
            # survives DB persistence and is checked correctly on reload.
            state.sales_actions.pending_confirmation.expires_at = action_plan.pending_expires_at

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
                # Synthetic lead (dev/test env with no lead_service): mark created so
                # post-lead flows work in test, but no real DB record exists.
                if action_result.payload.get("synthetic"):
                    state.lead_created = True
                    state.lead_created_acknowledged = True
                    state.lead_objective_stage = "lead_created"
                    state.sales_actions.lead.created = True
                    state.sales_actions.lead.last_updated_at = datetime.now(UTC)
                return

            state.sales_actions.lead.lead_id = str(lead.get("id") or action_result.result_id)
            state.lead_id = state.sales_actions.lead.lead_id
            state.lead_created = True
            state.lead_created_acknowledged = True
            state.lead_objective_stage = "lead_created"
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
            # Step 16: mark handoff as created so it doesn't retrigger on later turns.
            state.consultation_handoff_created = True
            state.consultation_handoff_action_id = action_result.result_id
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

    @staticmethod
    def _build_action_events(action_result: ActionResult | None) -> list[dict[str, object]]:
        if action_result is None or not action_result.success:
            return []

        if action_result.action_type == ActionType.CREATE_LEAD:
            lead_payload = action_result.payload.get("lead")
            if not isinstance(lead_payload, dict):
                return []
            return [
                {
                    "type": "lead_created",
                    "lead_id": str(lead_payload.get("id") or action_result.result_id or ""),
                    "created": bool(action_result.payload.get("created", True)),
                    "name": _optional_string(lead_payload.get("name")),
                    "email": _optional_string(lead_payload.get("email")),
                    "phone": _optional_string(lead_payload.get("phone")),
                    "preferred_contact_method": _optional_string(
                        lead_payload.get("preferred_contact_method")
                    ),
                }
            ]

        if action_result.action_type == ActionType.SCHEDULE_CONSULTATION:
            starts_at = action_result.payload.get("starts_at_utc")
            ends_at = action_result.payload.get("ends_at_utc")
            return [
                {
                    "type": "consultation_scheduled",
                    "appointment_id": str(action_result.result_id or ""),
                    "lead_id": str(action_result.payload.get("lead_id") or ""),
                    "csr_id": _optional_string(action_result.payload.get("csr_id")),
                    "csr_name": _optional_string(action_result.payload.get("csr_name")),
                    "starts_at_utc": str(starts_at) if starts_at is not None else None,
                    "ends_at_utc": str(ends_at) if ends_at is not None else None,
                    "customer_timezone": _optional_string(
                        action_result.payload.get("customer_timezone")
                    ),
                    "status": _optional_string(action_result.payload.get("status")),
                }
            ]

        return []

    @staticmethod
    def _create_lead_action_plan_from_contact(
        *,
        state: ThreadState,
        contact_capture: Any,
        intent: IntentVote,
    ) -> ActionPlan:
        service = intent.service_primary.value if intent.service_primary is not None else None
        return ActionPlan(
            action_type=ActionType.CREATE_LEAD,
            status=ActionStatus.READY,
            collected_slots={
                "name": contact_capture.contact.name,
                "email": contact_capture.contact.email,
                "phone": contact_capture.contact.phone,
                "services": [service] if service else [],
                "manuscript_status": (
                    str(state.project.manuscript_status.value)
                    if state.project.manuscript_status.value is not None
                    else None
                ),
                "message": state.rolling_summary or "",
            },
            reason="Lead objective engine marked contact as ready for lead creation.",
        )

    @staticmethod
    def _build_lead_intake_payload(
        *,
        state: ThreadState,
        message: str,
        intent: IntentVote,
        attachment_intake: AttachmentIntakeResult,
        contact_capture: Any,
        thread_id: UUID,
        customer_id: UUID | None,
    ) -> LeadIntakePayload:
        attachments = [a.model_dump(mode="json") for a in attachment_intake.attachments]
        return LeadIntakePayload(
            name=contact_capture.contact.name,
            email=contact_capture.contact.email,
            phone=contact_capture.contact.phone,
            message=message,
            service=intent.service_primary.value if intent.service_primary is not None else None,
            manuscript_status=(
                str(state.project.manuscript_status.value)
                if state.project.manuscript_status.value is not None
                else None
            ),
            assessment_type=attachment_intake.assessment_type,
            attachments=attachments,
            thread_id=str(thread_id),
            customer_id=str(customer_id) if customer_id is not None else None,
        )

    def _stabilize_service_context(
        self,
        *,
        intent: IntentVote,
        processed: object,
        state: ThreadState,
    ) -> IntentVote:
        from bookcraft.components.preprocessor.schemas import ProcessedMessage

        if not isinstance(processed, ProcessedMessage):
            return intent
        return ContextArbiter().arbitrate(intent=intent, processed=processed, state=state).intent

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

    async def _bg_trg_update_and_persist(
        self,
        *,
        thread_id: UUID,
        turn_sequence: int,
        user_text: str,
        assistant_text: str,
        previous_state: "ThreadState | None",
        state_deltas: list,
        arbiter_signals: list[str],
        preloaded_graph: "TemporalRelationGraph | None",
    ) -> None:
        if self.trg_engine is None:
            return
        try:
            trg_result = await self.trg_engine.update_after_turn(
                thread_id=thread_id,
                turn_sequence=turn_sequence,
                user_text=user_text,
                assistant_text=assistant_text,
                previous_state=previous_state,
                state_deltas=iter(state_deltas),
                arbiter_signals=arbiter_signals,
                preloaded_graph=preloaded_graph,
            )
            if self.trg_fact_store is not None and trg_result.graph is not None and trg_result.graph.semantic_facts:
                await self.trg_fact_store.upsert_facts(
                    thread_id=thread_id,
                    facts=trg_result.graph.semantic_facts,
                    turn_index=turn_sequence,
                )
        except Exception as exc:
            structlog.get_logger(__name__).warning(
                "bg_trg_update_failed",
                thread_id=str(thread_id),
                exception_class=exc.__class__.__name__,
            )

    async def _build_trg_response_hint(
        self,
        *,
        thread_id: UUID,
        state: ThreadState,
        intent: IntentVote,
    ) -> str | None:
        trg_context, _ = await self._build_trg_context(thread_id)
        return _trg_response_hint_from_context(
            state=state,
            intent=intent,
            trg_context=trg_context,
        )

    async def _build_trg_context(self, thread_id: UUID) -> tuple[TRGContext | None, TemporalRelationGraph | None]:
        if self.trg_engine is None:
            return None, None

        graph = await self.trg_engine.repository.load(thread_id)

        # Cold-start reload: if the hot graph is absent or has no semantic facts,
        # restore persisted facts from PostgreSQL so the bot retains context after
        # Redis TTL expiry.
        if (
            self.trg_fact_store is not None
            and (graph is None or not graph.semantic_facts)
        ):
            try:
                persisted_facts = await self.trg_fact_store.load_active_facts(thread_id)
                if persisted_facts:
                    if graph is None:
                        graph = TemporalRelationGraph(thread_id=thread_id)
                    graph.semantic_facts = persisted_facts
                    await self.trg_engine.repository.save(graph)
            except Exception as _csr_exc:  # noqa: BLE001
                structlog.get_logger(__name__).warning(
                    "trg_cold_start_reload_failed",
                    thread_id=str(thread_id),
                    exception_class=_csr_exc.__class__.__name__,
                )

        if graph is None:
            return None, None

        return self.trg_engine.build_context(graph), graph

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
        _buf: "_EventBuffer | None" = None,
    ) -> tuple[str, int, str]:
        safe_payload = redact_mapping(payload) or {}
        if self.thread_repository is None:
            memory = self.threads.setdefault(thread_id, ThreadMemory())
            event_hash = self._append_event(memory, thread_id, event_type, safe_payload)
            return event_hash, len(memory.events), event_hash

        # Buffered path: collect for batch flush later
        if _buf is not None and event_type not in _IMMEDIATE_EVENT_TYPES:
            return _buf.collect(
                thread_id=thread_id,
                sequence=sequence,
                event_type=event_type,
                payload=safe_payload,
                previous_hash=previous_hash,
            )

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
        fallback_decision: PortfolioFallbackDecision | None = None,
    ) -> PortfolioResponse | None:
        if self.portfolio_engine is None:
            return None

        # Determine service from fallback filters or normal flow.
        if fallback_decision is not None and fallback_decision.filters.get("service"):
            raw_svc = fallback_decision.filters["service"]
            try:
                service: ServiceCategory | None = ServiceCategory(str(raw_svc))
            except ValueError:
                service = intent_service
        else:
            service = intent_service or (
                state.project.services_discussed[0].service.value
                if state.project.services_discussed
                and state.project.services_discussed[0].service.value is not None
                else None
            )
        if service is None:
            return None

        # For fallback_general/service_samples: skip genre filter.
        if fallback_decision is not None and fallback_decision.strategy in (
            "fallback_general_samples",
            "fallback_service_samples",
        ):
            genre: str | None = None
        elif fallback_decision is not None and fallback_decision.filters.get("genre"):
            genre = str(fallback_decision.filters["genre"])
        else:
            genre = state.project.genre.value or _genre_from_text(message)

        request = PortfolioRequest(
            service=ServiceCategory(str(service)),
            genre=genre,
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


_PROOF_COPY_RE = __import__("re").compile(
    r"\b(?:"
    r"print\s+a\s+sample|sample\s+copy|proof\s+copy|physical\s+proof|"
    r"print\s+(?:my|a\s+copy|copies\s+of\s+my)|"
    r"sample\s+print|test\s+print|print\s+run|advance\s+copy|advance\s+reader|"
    r"publish\s+(?:a\s+)?journal|journal\s+publish"
    r")\b",
    __import__("re").IGNORECASE,
)


def _is_proof_copy_request(text: str) -> bool:
    """Return True when the user is asking for a proof/sample of THEIR OWN book.

    These are publishing-service questions, NOT requests to see BookCraft's
    portfolio samples. Prevents the portfolio engine from firing on:
    'I want to print a sample', 'sample copy', 'proof copy', 'publish a journal'.
    """
    return bool(_PROOF_COPY_RE.search(text))


def _summarize_trimatch_payload(result: "TriMatchResult") -> dict[str, object]:
    return {
        "service_primary": result.service_primary.value if result.service_primary else None,
        "query_primary": result.query_primary.value if result.query_primary else None,
        "confidence": result.confidence,
        "evidence_summary": [
            {
                "rule_id": e.rule_id,
                "target": e.target,
                "layer": e.layer.value if e.layer else None,
                "confidence": e.confidence,
            }
            for e in result.evidence
        ],
    }


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _pending_confirmation_debug(state: ThreadState) -> dict[str, Any] | None:
    """Diagnostic view of the pending action confirmation (for /debug/state)."""
    from bookcraft.components.actions.slot_resolver import is_pending_expired

    pend = getattr(getattr(state, "sales_actions", None), "pending_confirmation", None)
    if pend is None or getattr(pend, "type", None) is None:
        return None
    try:
        expired = is_pending_expired(pend)
    except Exception:  # noqa: BLE001
        expired = None
    return {
        "type": getattr(pend, "type", None),
        "has_payload": bool(getattr(pend, "payload", None)),
        "expires_at": str(getattr(pend, "expires_at", None)),
        "expired": expired,
    }


def _sync_contact_capture_to_state(
    state: ThreadState,
    contact_capture: Any,  # ContactCaptureResult
) -> None:
    """Sync ContactCaptureResult into personal FieldMeta and sales_actions.lead.

    Phase 4 hotfix: contact_slots() reads from state.personal.* and
    state.sales_actions.lead.* but these are ONLY written by the state_applier
    (extraction deltas) — never by contact_capture.merge_with_state().
    This helper closes that gap so all downstream consumers see the same contact.

    Rules:
    - Only writes real (non-redacted, non-empty) values.
    - Never overwrites an existing higher-confidence value.
    - Does not log raw PII.
    """
    from bookcraft.components.leads.contact_utils import is_real_contact_value
    from bookcraft.domain.enums import Source
    from bookcraft.domain.meta import FieldMeta

    c = contact_capture.contact

    def _should_write(existing_field_meta: FieldMeta[str], new_value: str | None) -> bool:
        if not new_value or not is_real_contact_value(new_value):
            return False
        existing = getattr(existing_field_meta, "value", None)
        if existing and is_real_contact_value(existing):
            return False  # Already have a real value; don't overwrite.
        return True

    if c.name and _should_write(state.personal.name, c.name):
        state.personal.name = FieldMeta[str](
            value=c.name,
            confidence=0.92,
            source=Source.USER_STATED,
            extracted_by="contact_capture_sync",
        )

    if c.email and _should_write(state.personal.email, c.email):
        state.personal.email = FieldMeta[str](
            value=c.email,
            confidence=0.98,
            source=Source.USER_STATED,
            extracted_by="contact_capture_sync",
        )

    if c.phone and _should_write(state.personal.phone, c.phone):
        state.personal.phone = FieldMeta[str](
            value=c.phone,
            confidence=0.95,
            source=Source.USER_STATED,
            extracted_by="contact_capture_sync",
        )

    # Also sync to sales_actions.lead (used by contact_slots priority 4).
    if c.name and not state.sales_actions.lead.name:
        state.sales_actions.lead.name = c.name
    if c.email and not state.sales_actions.lead.email:
        state.sales_actions.lead.email = c.email
    if c.phone and not state.sales_actions.lead.phone:
        state.sales_actions.lead.phone = c.phone


def _reconcile_consultation_action_plan(
    *,
    current_plan: ActionPlan,
    consultation_decision: ConsultationStateDecision,
    state: Any,  # ThreadState
    contact_capture: Any,  # ContactCaptureResult
) -> ActionPlan:
    """Override action_plan to SCHEDULE_CONSULTATION when reducer says all details are ready.

    Only overrides if the current plan is not already a valid schedule plan.
    Never touches contact or manuscript discovery — consultation takes priority.
    """
    if not consultation_decision.can_schedule:
        return current_plan

    if current_plan.action_type == ActionType.SCHEDULE_CONSULTATION and current_plan.status in {
        ActionStatus.READY,
        ActionStatus.NEEDS_CONFIRMATION,
    }:
        return current_plan

    ci = getattr(state, "contact_info", None) or {}
    c = getattr(contact_capture, "contact", None)
    name = (
        (getattr(c, "name", None) if c else None)
        or ci.get("name")
        or getattr(getattr(state.personal, "name", None), "value", None)
        or getattr(state.sales_actions.lead, "name", None)
    )
    email = (
        (getattr(c, "email", None) if c else None)
        or ci.get("email")
        or getattr(getattr(state.personal, "email", None), "value", None)
        or getattr(state.sales_actions.lead, "email", None)
    )
    phone = (
        (getattr(c, "phone", None) if c else None)
        or ci.get("phone")
        or getattr(getattr(state.personal, "phone", None), "value", None)
        or getattr(state.sales_actions.lead, "phone", None)
    )

    # Resolve customer timezone from state — prefer personal.timezone (LLM-extracted),
    # fall back to previously stored consultation timezone, then default to Chicago.
    customer_timezone = (
        getattr(getattr(state.personal, "timezone", None), "value", None)
        or getattr(state.sales_actions.consultation, "customer_timezone", None)
        or "America/Chicago"
    )

    slots: dict[str, Any] = {
        "requested_time_text": consultation_decision.preferred_call_time or "",
        "customer_timezone": customer_timezone,
    }
    if name:
        slots["name"] = name
    if email:
        slots["email"] = email
        slots["email_or_phone"] = email
    if phone:
        slots["phone"] = phone
        if not email:
            slots["email_or_phone"] = phone

    # Carry the confirmation key + expiry so the "yes" next turn is actually consumable.
    # Without these, _apply_sales_action_plan_to_state sets the consultation STAGE to
    # pending_confirmation but never sets sales_actions.pending_confirmation.type, so the
    # planner can't match the confirmation and the booking never executes (BUG-6040).
    from bookcraft.components.actions.slot_resolver import make_pending_expires_at

    return ActionPlan(
        action_type=ActionType.SCHEDULE_CONSULTATION,
        status=ActionStatus.NEEDS_CONFIRMATION,
        collected_slots=slots,
        confirmation_required=True,
        pending_confirmation_key=ActionType.SCHEDULE_CONSULTATION.value,
        pending_expires_at=make_pending_expires_at(ActionType.SCHEDULE_CONSULTATION.value),
        reason="consultation_state_reducer:can_schedule",
    )


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


def _trg_response_hint_from_context(
    *,
    state: ThreadState,
    intent: IntentVote,
    trg_context: TRGContext | None,
) -> str | None:
    state_hint = _state_context_response_hint(state, intent)
    parts: list[str] = []

    if state_hint:
        parts.append(state_hint)

    if trg_context is not None and trg_context.outstanding_questions:
        parts.append(
            "Previous assistant questions already asked: "
            + " | ".join(trg_context.outstanding_questions[-3:])
        )

    if trg_context is not None and trg_context.repeated_user_messages:
        parts.append(
            "The user appears to be repeating themselves. Do not ask the same "
            "question again; acknowledge the repeated information and move forward."
        )

    # Gap 7: surface specific contradiction details so the LLM can reconcile gently.
    if trg_context is not None and trg_context.contradictions:
        contradiction_details = []
        for c in trg_context.contradictions[:2]:  # cap at 2 to stay concise
            if getattr(c, "resolution_status", "unresolved") == "unresolved":
                path = getattr(c, "fact_path", "a project detail")
                old_val = getattr(c, "old_value", None)
                new_val = getattr(c, "new_value", None)
                if old_val and new_val:
                    contradiction_details.append(f"{path}: earlier='{old_val}' vs now='{new_val}'")
        if contradiction_details:
            parts.append(
                "Unresolved contradictions detected — surface gently to reconcile: "
                + "; ".join(contradiction_details)
            )
        elif trg_context.contradiction_count:
            parts.append(
                "There may be contradictory project details. Surface them gently "
                "('Earlier you mentioned X — should I use Y instead?') rather than "
                "silently picking one value."
            )

    # Gap 7: note recent service shift so the LLM cleanly switches focus.
    if trg_context is not None and trg_context.service_shifts:
        latest_shift = trg_context.service_shifts[-1]
        old_svc = getattr(latest_shift, "previous_service", None)
        new_svc = getattr(latest_shift, "new_service", None)
        if new_svc and old_svc and old_svc != new_svc:
            parts.append(
                f"Service focus shifted from '{old_svc}' to '{new_svc}'. "
                f"Drop scoping for '{old_svc}' and move cleanly into '{new_svc}'."
            )

    if not parts:
        return None

    return " ".join(parts)


def _build_csr_context_hint(state: ThreadState) -> str | None:
    """Build a CSR context section for the response prompt.

    Includes the abstract, last 3 verbatim turns, and commitments (read-only).
    Only injected when CSR turns have been ingested.
    """
    if not state.csr_turns_ingested:
        return None

    parts: list[str] = ["--- CSR Conversation Context ---"]

    if state.csr_context_abstract:
        parts.append(f"[Summary of earlier CSR conversation]: {state.csr_context_abstract}")

    if state.csr_context_recent_verbatim:
        parts.append("Recent CSR exchanges (verbatim):")
        for turn in state.csr_context_recent_verbatim[-3:]:
            csr_name = turn.get("csr_name", "CSR")
            csr_msg = turn.get("csr_message", "")
            user_msg = turn.get("user_message", "")
            line = f"  {csr_name}: {csr_msg[:200]}"
            if user_msg:
                line += f" | User: {user_msg[:100]}"
            parts.append(line)

    if state.csr_commitments:
        parts.append("CSR Commitments (do NOT re-commit or contradict — acknowledge as agreed):")
        for c in state.csr_commitments[:5]:
            ctype = c.get("type", "commitment")
            text = c.get("text", "")
            parts.append(f"  • {ctype.replace('_', ' ').title()}: \"{text}\"")

    return "\n".join(parts)


def _build_entity_context_hint(
    entity_docs: list[dict[str, object]],
    state: ThreadState,
) -> str | None:
    """Build a prompt hint from retrieved entity memory, injecting only NEW facts.

    Skips facts already present in thread state to prevent prompt bloat.
    """
    if not entity_docs:
        return None

    # Collect state paths already in known_facts via FieldMeta
    known_paths: set[str] = set()
    for owner_name in ("personal", "project", "commercial"):
        owner = getattr(state, owner_name, None)
        if owner is None:
            continue
        for field_name in vars(owner):
            fm = getattr(owner, field_name, None)
            val = getattr(fm, "value", None) if fm is not None else None
            if val is not None:
                known_paths.add(f"{owner_name}.{field_name}")

    new_facts: list[str] = []
    for doc in entity_docs:
        path = str(doc.get("entity_path", ""))
        value = str(doc.get("entity_value_text", ""))
        if not path or not value:
            continue
        if path in known_paths:
            continue
        label = path.split(".")[-1].replace("_", " ")
        new_facts.append(f"{label}: {value}")

    if not new_facts:
        return None

    lines = "; ".join(new_facts[:5])  # cap at 5 to avoid prompt bloat
    return f"Additional recalled facts (from conversation memory): {lines}."


# ---------------------------------------------------------------------------
# Phase 12 PR 7: project/slot event helpers for TRG trace enrichment
# ---------------------------------------------------------------------------


def _build_project_shifts(project_snapshot: Any) -> list[dict[str, Any]]:
    if project_snapshot is None:
        return []
    decision = getattr(project_snapshot, "decision", None)
    if decision is None:
        return []
    event = getattr(decision, "event", None)
    if not event or event == "same_project":
        return []
    shift = ProjectShiftEvent(
        previous_project_id=getattr(decision, "previous_project_id", None),
        new_project_id=getattr(decision, "active_project_id", None),
        event=str(event),
        audit=list(getattr(decision, "audit", [])),
    )
    return [shift.model_dump(mode="json")]


def _build_slot_resolutions(
    slot_statuses: list[Any],
    project_snapshot: Any,
) -> list[dict[str, Any]]:
    if not slot_statuses:
        return []
    active_id: str | None = None
    if project_snapshot is not None:
        active_id = getattr(project_snapshot, "active_project_id", None)
    result: list[dict[str, Any]] = []
    for s in slot_statuses:
        ev = SlotResolutionEvent(
            project_id=active_id,
            slot=s.slot,
            status=s.status,
            source_turn_id=s.source_turn_id,
            forbidden_reask=s.forbidden_reask,
        )
        result.append(ev.model_dump(mode="json"))
    return result


def _build_delegations(
    delegated_decision: Any,
    project_snapshot: Any,
) -> list[dict[str, Any]]:
    if delegated_decision is None:
        return []
    if not getattr(delegated_decision, "detected", False):
        return []
    active_id: str | None = None
    if project_snapshot is not None:
        active_id = getattr(project_snapshot, "active_project_id", None)
    ev = DelegationEvent(
        project_id=active_id,
        slot=getattr(delegated_decision, "target_slot", None),
        status=str(getattr(delegated_decision, "status", "not_delegated")),
    )
    return [ev.model_dump(mode="json")]


# ---------------------------------------------------------------------------
# Proactive greeting helpers (used by ChatService.handle_greet)
# ---------------------------------------------------------------------------

# Single fallback used ONLY when the LLM call fails entirely.
_GREET_FALLBACK = (
    "Hello there! Are you exploring professional publishing services? "
    "I'd be happy to help you find the right publishing plan for your manuscript."
)


def _normalise_page_slug(page: str | None) -> str | None:
    """Strip protocol, domain, leading slashes, and query string from a URL."""
    if not page:
        return None
    import re as _re
    slug = _re.sub(r"^https?://[^/]+", "", page.strip().lower())
    slug = slug.strip("/").split("?")[0].split("#")[0]
    return slug.replace("_", "-") or None


class _GreetingResponse(BaseModel):
    """Thin wrapper so the structured LLM adapter can return a plain text greeting."""

    text: str = ""


async def _llm_proactive_greeting(
    *,
    adapter: Any,
    landing_page: str | None,
    landing_keyword: str | None,
) -> str:
    """Generate a personalised first message via the LLM.

    Always attempts the LLM call.  Only falls back to ``_GREET_FALLBACK`` when
    the call raises or returns an unusable response.
    """
    page_label = _normalise_page_slug(landing_page) or "homepage"
    keyword_line = (
        f"\nSearch keyword that brought them here: {landing_keyword}"
        if landing_keyword
        else ""
    )

    system = (
        "You are a BookCraft AI chat assistant. "
        "BookCraft is a full-service publishing company that helps authors write, edit, design, "
        "format, and publish their books.\n\n"
        "Your task: write ONE warm, professional opening message for a visitor who just opened "
        "the chat widget. Return a JSON object with a single key: {\"text\": \"<your message>\"}.\n\n"
        "Rules:\n"
        "- 1–2 sentences, 30 words maximum.\n"
        "- Never quote a price or make a commitment.\n"
        "- No markdown, bullet points, or headings.\n"
        "- Sound like a knowledgeable, friendly colleague — not a sales bot.\n"
        "- End with one open question inviting the visitor to share what they need.\n"
        "- Do not start with 'I'."
    )
    user = (
        f"Landing page: {page_label}{keyword_line}\n\n"
        "Return the JSON greeting now."
    )

    try:
        result = await adapter.structured(
            system=system,
            user=user,
            output_model=_GreetingResponse,
            purpose="proactive_greeting",
        )
        text = (getattr(result, "text", None) or "").strip()
        if text and len(text) > 10:
            return text
    except Exception:
        pass

    return _GREET_FALLBACK
