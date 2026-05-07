from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from prometheus_client import Counter, Histogram

from bookcraft.components.extraction import CombinedExtractor, StateApplier
from bookcraft.components.intent import HaikuIntentClassifier
from bookcraft.components.language_guard import LanguageGuard
from bookcraft.components.preprocessor import SharedPreprocessor
from bookcraft.components.rag.retriever import RagRetriever
from bookcraft.components.response import ResponseFormatter, SonnetResponseGenerator
from bookcraft.components.storage.events import calculate_event_hash
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
            draft = await self.response_generator.generate(
                message=processed,
                state=memory.state,
                intent=intent,
                extraction=extraction,
                rag_chunks=rag_chunks,
            )
            bubbles = self.formatter.format(draft.text)
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
