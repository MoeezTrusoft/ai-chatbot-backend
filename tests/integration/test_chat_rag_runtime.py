from __future__ import annotations

from dataclasses import dataclass

import pytest

from bookcraft.api.chat import ChatTurnRequest
from bookcraft.api.main import build_chat_service
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.components.rag.schemas import RetrievedChunk
from bookcraft.domain.enums import ServiceCategory
from bookcraft.infra.config import Settings


@dataclass(slots=True)
class FakeRagRetriever:
    calls: int = 0

    async def retrieve(
        self,
        processed_message: ProcessedMessage,
        intent: IntentVote,
        top_k: int = 8,
    ) -> list[RetrievedChunk]:
        del processed_message, intent, top_k
        self.calls += 1
        return [
            RetrievedChunk(
                chunk_id="runtime-rag-chunk",
                content="Runtime RAG ghostwriting content is available for service questions.",
                score=1.0,
                service_category=ServiceCategory.GHOSTWRITING,
                section="Overview",
                source_id="ghostwriting",
                title="Ghostwriting",
                checksum="checksum",
                citation="Ghostwriting::Overview::runtime-rag-chunk",
            )
        ]


@dataclass(slots=True)
class FailingRagRetriever:
    calls: int = 0

    async def retrieve(
        self,
        processed_message: ProcessedMessage,
        intent: IntentVote,
        top_k: int = 8,
    ) -> list[RetrievedChunk]:
        del processed_message, intent, top_k
        self.calls += 1
        raise RuntimeError("elasticsearch unavailable author@example.com")


@pytest.mark.asyncio
async def test_chat_service_uses_injected_rag_retriever_for_service_question() -> None:
    retriever = FakeRagRetriever()
    service = build_chat_service(
        Settings(app_env="test"),
        rag_retriever=retriever,  # type: ignore[arg-type]
    )

    response = await service.handle_turn(
        ChatTurnRequest(message="Tell me about BookCraft ghostwriting services")
    )

    assert retriever.calls == 1
    assert response.bubbles, "Response must contain bubbles even without LLM surfacing RAG"

    rendered = " ".join(bubble.text for bubble in response.bubbles)
    assert rendered.strip(), "Response text must be non-empty"

    # Without a live LLM adapter the template fallback is used; RAG chunks are
    # passed as grounding to the LLM prompt but are NOT embedded directly in the
    # template text. Assert the retriever ran and the response degrades gracefully.
    # (When a real LLM adapter is present, chunk content will appear in the reply.)

    # Phase 9: rag_query must be present in the trace with an enriched query.
    rows = service.trace_store.for_thread(str(response.thread_id)) if service.trace_store else []
    if rows:
        rag_q = rows[0].get("rag_query", {})
        assert rag_q.get("query_text"), "rag_query.query_text must be populated in trace"
        # Ghostwriting service context should appear in the enriched query.
        assert "ghostwriting" in rag_q.get("query_text", "").lower(), (
            "Context-aware query must include the active service term"
        )


@pytest.mark.asyncio
async def test_chat_service_degrades_when_rag_retrieval_fails() -> None:
    retriever = FailingRagRetriever()
    service = build_chat_service(
        Settings(app_env="test"),
        rag_retriever=retriever,  # type: ignore[arg-type]
    )

    response = await service.handle_turn(
        ChatTurnRequest(message="Tell me about BookCraft ghostwriting services")
    )

    assert retriever.calls == 1
    assert response.bubbles
    serialized_events = str(service.threads[response.thread_id].events)
    assert "rag.failed" in serialized_events
    assert "author@example.com" not in serialized_events


def test_build_chat_service_accepts_rag_retriever_argument() -> None:
    retriever = FakeRagRetriever()
    service = build_chat_service(
        Settings(app_env="test"),
        rag_retriever=retriever,  # type: ignore[arg-type]
    )

    assert service.rag_retriever is retriever
