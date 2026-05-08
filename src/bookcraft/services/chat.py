from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from prometheus_client import Counter, Histogram

from bookcraft.components.extraction import CombinedExtractor, StateApplier
from bookcraft.components.extraction.schemas import CombinedExtraction, StateDelta
from bookcraft.components.intent import HaikuIntentClassifier
from bookcraft.components.language_guard import LanguageGuard
from bookcraft.components.preprocessor import SharedPreprocessor
from bookcraft.components.pricing import (
    PricingQuoteRequest,
    PricingTimelineEngine,
    PricingTimelineQuote,
)
from bookcraft.components.rag.retriever import RagRetriever
from bookcraft.components.response import ResponseFormatter, SonnetResponseGenerator
from bookcraft.components.storage.events import calculate_event_hash
from bookcraft.components.trg import TemporalRelationGraphEngine
from bookcraft.components.trimatch import TriMatchEngine
from bookcraft.domain.enums import QueryIntentType, Source
from bookcraft.domain.state import ThreadState

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
    intent_classifier: HaikuIntentClassifier
    extractor: CombinedExtractor
    state_applier: StateApplier
    response_generator: SonnetResponseGenerator
    formatter: ResponseFormatter
    rag_retriever: RagRetriever | None = None
    pricing_engine: PricingTimelineEngine | None = None
    trg_engine: TemporalRelationGraphEngine | None = None
    trimatch_engine: TriMatchEngine | None = None
    threads: dict[UUID, ThreadMemory] = field(default_factory=dict)

    async def handle_turn(self, payload: ChatTurnRequest) -> ChatTurnResponse:
        from bookcraft.api.chat import ChatTurnResponse

        CHAT_TURNS_TOTAL.inc()
        with CHAT_TURN_LATENCY.time():
            thread_id = payload.thread_id or uuid4()
            memory = self.threads.setdefault(thread_id, ThreadMemory())
            language = self.language_guard.detect(payload.message, cached_language="en")
            event_ids = [
                self._append_event(memory, thread_id, "user.message", {"text": payload.message})
            ]
            if not language.is_english:
                bubbles = self.formatter.format(language.redirect_message or "")
                event_ids.append(
                    self._append_event(
                        memory,
                        thread_id,
                        "assistant.redirect",
                        {"language": language.language},
                    )
                )
                return ChatTurnResponse(
                    thread_id=thread_id,
                    bubbles=bubbles,
                    intent=None,
                    language_status=language.language,
                    debug_event_ids=event_ids,
                )

            processed = await self.preprocessor.process(payload.message, language=language.language)
            if self.trimatch_engine is not None:
                trimatch_result = self.trimatch_engine.classify(processed)
                event_ids.append(
                    self._append_event(
                        memory,
                        thread_id,
                        "trimatch.voted",
                        trimatch_result.model_dump(mode="json"),
                    )
                )
            intent = await self.intent_classifier.classify(processed, memory.state)
            event_ids.append(
                self._append_event(
                    memory,
                    thread_id,
                    "intent.classified",
                    {"intent": intent.model_dump(mode="json")},
                )
            )
            extraction = await self.extractor.extract(processed, memory.state)
            previous_state = memory.state.model_copy(deep=True)
            memory.state = self.state_applier.apply(memory.state, extraction)
            STATE_UPDATES.labels(result="applied").inc()
            event_ids.append(
                self._append_event(
                    memory,
                    thread_id,
                    "extraction.applied",
                    {"delta_count": len(extraction.state_deltas)},
                )
            )
            rag_chunks = []
            if self.rag_retriever is not None:
                rag_chunks = await self.rag_retriever.retrieve(processed, intent)
            pricing_quote: PricingTimelineQuote | None = None
            timeline_estimate: PricingTimelineQuote | None = None
            pricing_missing_question: str | None = None
            if intent.query_primary in {
                QueryIntentType.PRICING_QUESTION,
                QueryIntentType.TIMELINE_QUESTION,
            }:
                pricing_quote, timeline_estimate, pricing_missing_question = self._price_turn(
                    thread_id=thread_id,
                    state=memory.state,
                    intent_service=intent.service_primary,
                    message=payload.message,
                    confidence=intent.confidence,
                )
                if pricing_quote is not None:
                    memory.state = self.state_applier.apply(
                        memory.state,
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
            draft = await self.response_generator.generate(
                message=processed,
                state=memory.state,
                intent=intent,
                extraction=extraction,
                rag_chunks=rag_chunks,
                pricing_quote=pricing_quote,
                timeline_estimate=timeline_estimate,
                pricing_missing_question=pricing_missing_question,
            )
            bubbles = self.formatter.format(draft.text)
            if self.trg_engine is not None:
                trg_result = await self.trg_engine.update_after_turn(
                    thread_id=thread_id,
                    turn_sequence=len(memory.events) + 1,
                    user_text=payload.message,
                    assistant_text=draft.text,
                    previous_state=previous_state,
                    state_deltas=extraction.state_deltas,
                )
                event_ids.append(
                    self._append_event(
                        memory,
                        thread_id,
                        "trg.updated",
                        {
                            "node_count": len(trg_result.graph.nodes),
                            "edge_count": len(trg_result.graph.edges),
                            "unresolved_question_count": trg_result.unresolved_question_count,
                            "contradiction_count": trg_result.contradiction_count,
                        },
                    )
                )
            event_ids.append(
                self._append_event(
                    memory,
                    thread_id,
                    "assistant.response",
                    {
                        "intent": intent.model_dump(mode="json"),
                        "bubble_count": len(bubbles),
                        "source": draft.source,
                    },
                )
            )
            return ChatTurnResponse(
                thread_id=thread_id,
                bubbles=bubbles,
                intent=intent,
                language_status=language.language,
                debug_event_ids=event_ids,
            )

    @staticmethod
    def _append_event(
        memory: ThreadMemory,
        thread_id: UUID,
        event_type: str,
        payload: dict[str, object],
    ) -> str:
        sequence = len(memory.events) + 1
        previous_hash = str(memory.events[-1]["event_hash"]) if memory.events else None
        event_hash = calculate_event_hash(
            thread_id=thread_id,
            sequence=sequence,
            event_type=event_type,
            payload=payload,
            previous_hash=previous_hash,
        )
        memory.events.append(
            {
                "sequence": sequence,
                "event_type": event_type,
                "payload": payload,
                "previous_hash": previous_hash,
                "event_hash": event_hash,
            }
        )
        return event_hash

    def _price_turn(
        self,
        *,
        thread_id: UUID,
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
            timeline = self.pricing_engine.quote(request)
            if timeline.missing_inputs:
                return None, None, timeline.missing_inputs[0].question
            return None, timeline, None
        quote = self.pricing_engine.quote(request)
        if quote.missing_inputs:
            return None, None, quote.missing_inputs[0].question
        return quote, None, None


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
