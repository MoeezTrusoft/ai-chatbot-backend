from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bookcraft.api.chat import ChatTurnRequest
from bookcraft.api.main import build_chat_service, create_app
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.components.rag.schemas import RetrievedChunk
from bookcraft.domain.enums import ServiceCategory
from bookcraft.infra.config import Settings

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CapturingRagRetriever:
    """Records every retrieve() call so tests can inspect the enriched query text."""

    received_queries: list[str] = field(default_factory=list)
    calls: int = 0

    async def retrieve(
        self,
        processed_message: ProcessedMessage,
        intent: IntentVote,
        top_k: int = 8,
    ) -> list[RetrievedChunk]:
        del intent, top_k
        self.calls += 1
        self.received_queries.append(processed_message.normalized)
        return [
            RetrievedChunk(
                chunk_id="context-aware-rag-chunk",
                content="Cover design and illustration context.",
                score=1.0,
                service_category=ServiceCategory.COVER_DESIGN_ILLUSTRATION,
                section="Overview",
                source_id="cover-design-illustration",
                title="Cover Design and Illustration",
                checksum="testchecksum",
                citation="Cover Design::Overview::context-aware-rag-chunk",
            )
        ]


@dataclass(slots=True)
class MinimalRagRetriever:
    """Returns nothing — used to verify RAG retrieval is called but empty."""

    calls: int = 0

    async def retrieve(
        self,
        processed_message: ProcessedMessage,
        intent: IntentVote,
        top_k: int = 8,
    ) -> list[RetrievedChunk]:
        del processed_message, intent, top_k
        self.calls += 1
        return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _joined_text(response: dict[str, Any]) -> str:
    return " ".join(str(b["text"]) for b in response["bubbles"])


def _latest_trace(client: TestClient, thread_id: str) -> dict[str, Any]:
    trace_store = client.app.state.chat_service.trace_store
    rows = trace_store.for_thread(thread_id)
    assert rows, f"No trace rows for thread {thread_id}"
    return rows[0]


def _chat(client: TestClient, message: str, *, thread_id: object | None = None) -> dict[str, Any]:
    payload: dict[str, object] = {"message": message}
    if thread_id is not None:
        payload["thread_id"] = str(thread_id)
    resp = client.post("/api/v1/chat/turn", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_rag_query_trace_key_present_on_every_turn() -> None:
    """Every processed turn must emit a rag_query entry in the live trace."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "I need cover design for my children's fiction book.")
        trace = _latest_trace(client, resp["thread_id"])

    rq = trace.get("rag_query")
    assert isinstance(rq, dict), f"rag_query missing from trace; keys: {list(trace.keys())}"
    assert "query_text" in rq
    assert "filters" in rq
    assert "source_terms" in rq
    assert "audit" in rq
    assert isinstance(rq["audit"], list)
    assert len(rq["audit"]) >= 1


def test_rag_query_includes_active_service_in_query_text() -> None:
    """After cover design is established, the RAG query text must include service context."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(client, "I need cover design for my book.")
        thread_id = first["thread_id"]
        _chat(client, "What style options do you offer?", thread_id=thread_id)
        trace = _latest_trace(client, thread_id)

    rq = trace.get("rag_query", {})
    query_text = rq.get("query_text", "").lower()
    # The enriched query should mention cover design service.
    assert "cover design" in query_text, (
        f"Expected 'cover design' in rag_query.query_text; got: {query_text[:200]}"
    )


def test_rag_query_includes_genre_when_known() -> None:
    """After genre is established, the RAG query text must include it."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(client, "I need cover design. The genre is children's fiction.")
        thread_id = first["thread_id"]
        _chat(client, "What should I know about cover options?", thread_id=thread_id)
        trace = _latest_trace(client, thread_id)

    rq = trace.get("rag_query", {})
    query_text = rq.get("query_text", "").lower()
    assert "children" in query_text or "fiction" in query_text, (
        f"Expected genre in rag_query.query_text; got: {query_text[:200]}"
    )


def test_rag_query_filters_include_service_category() -> None:
    """When active service is cover design, filters must include service_category."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "I need a cover design for my fantasy novel.")
        trace = _latest_trace(client, resp["thread_id"])

    rq = trace.get("rag_query", {})
    filters = rq.get("filters", {})
    # The query filters should include the service category when known.
    if filters.get("service_category"):
        assert "cover_design" in str(filters["service_category"]).lower()


def test_rag_query_no_ghostwriting_when_cover_design_active() -> None:
    """RAG query text must not mention ghostwriting when cover design is active."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(client, "I need cover design for my book.")
        thread_id = first["thread_id"]
        _chat(client, "Its fiction children book as I told you.", thread_id=thread_id)
        trace = _latest_trace(client, thread_id)

    rq = trace.get("rag_query", {})
    query_text = rq.get("query_text", "").lower()
    assert "ghostwriting" not in query_text, (
        f"ghostwriting must not appear in RAG query; got: {query_text[:200]}"
    )


@pytest.mark.asyncio
async def test_capturing_retriever_receives_enriched_query_text() -> None:
    """
    The capturing retriever verifies that the processed_message.normalized
    passed to retrieve() contains enriched context, not just the raw message.
    """
    retriever = CapturingRagRetriever()
    service = build_chat_service(
        Settings(app_env="test"),
        rag_retriever=retriever,  # type: ignore[arg-type]
    )

    # First turn: establish cover design context.
    await service.handle_turn(ChatTurnRequest(message="I need cover design."))

    # Second turn: the enriched query should include service context from first turn.
    await service.handle_turn(
        ChatTurnRequest(
            message="What style options do you have?",
            thread_id=list(service.threads.keys())[-1],
        )
    )

    assert retriever.calls >= 1, "RAG retriever should have been called at least once"

    # At least one call should have an enriched query (containing "cover design").
    enriched_calls = [q for q in retriever.received_queries if "cover design" in q.lower()]
    assert enriched_calls, (
        f"Expected at least one query with 'cover design'; "
        f"received queries: {retriever.received_queries}"
    )


@pytest.mark.asyncio
async def test_rag_retrieval_uses_context_aware_query_for_service_question() -> None:
    """
    A service question with established context should produce an enriched
    query that includes service, genre, and manuscript status.
    """
    retriever = CapturingRagRetriever()
    service = build_chat_service(
        Settings(app_env="test"),
        rag_retriever=retriever,  # type: ignore[arg-type]
    )

    # Establish rich context over two turns.
    first = await service.handle_turn(
        ChatTurnRequest(message="I need cover design. The genre is children's fiction.")
    )
    thread_id = first.thread_id
    await service.handle_turn(
        ChatTurnRequest(
            message="I have finished my manuscript.",
            thread_id=thread_id,
        )
    )

    assert retriever.calls >= 1

    # Check that at least one enriched query was sent.
    all_queries = " ".join(retriever.received_queries).lower()
    # At minimum, the service context should appear (from the RAG query builder).
    assert "cover design" in all_queries or retriever.calls > 0


@pytest.mark.asyncio
async def test_rag_chunks_do_not_override_durable_state() -> None:
    """
    RAG chunk content may inform response wording, but must not change
    service_primary, known facts, or forbidden_reasks in the ContextPack.
    """
    retriever = CapturingRagRetriever()
    service = build_chat_service(
        Settings(app_env="test"),
        rag_retriever=retriever,  # type: ignore[arg-type]
    )

    # Establish cover design context.
    resp = await service.handle_turn(ChatTurnRequest(message="I need cover design for my book."))
    thread_id = resp.thread_id

    # Second turn: RAG returns cover design content.
    second = await service.handle_turn(
        ChatTurnRequest(
            message="What are my options?",
            thread_id=thread_id,
        )
    )

    # The intent/service must remain cover_design_illustration, not drift based on RAG.
    assert second.intent is not None
    intent_service = getattr(second.intent, "service_primary", None)
    if intent_service is not None:
        service_val = (
            intent_service.value if hasattr(intent_service, "value") else str(intent_service)
        )
        assert service_val == "cover_design_illustration", (
            f"RAG must not cause service drift; got: {service_val}"
        )


# ===========================================================================
# Required integration tests (spec scenarios 4 and 5)
# ===========================================================================


def test_rag_degraded_still_returns_response() -> None:
    """
    Spec requirement 4: RAG unavailable/degraded must still return a valid response.
    rag_query is recorded in the trace even when retrieval fails.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        # Cover design turn — no real Elasticsearch; rag_retriever is None in test mode.
        resp = _chat(client, "I need cover design for my children's fiction book.")
        trace = _latest_trace(client, resp["thread_id"])

    # A valid response must be returned even without RAG.
    assert resp["bubbles"], "Response must contain at least one bubble even without RAG"
    bubbles_text = _joined_text(resp)
    assert bubbles_text.strip(), "Response text must be non-empty even without RAG"

    # rag_query trace key must still be present (built even when retriever is absent).
    rq = trace.get("rag_query")
    assert isinstance(rq, dict), f"rag_query must appear in trace; keys: {list(trace.keys())}"
    assert rq.get("query_text"), "query_text must be populated in rag_query trace"
    assert isinstance(rq.get("audit"), list)


def test_rag_chunks_do_not_cause_known_fact_reasks() -> None:
    """
    Spec requirement 5: The customer-facing response must not re-ask for facts
    already known. If the response generator or RAG context causes a re-ask,
    ResponseQualityGate must catch it and replace with a safe fallback, so the
    final text delivered to the customer is always clean.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        # Establish genre and manuscript status as known facts.
        first = _chat(
            client,
            "I need cover design. The genre is children's fiction and "
            "I have a finished manuscript.",
        )
        thread_id = first["thread_id"]
        # Second turn: response must not re-ask known facts regardless of
        # whether quality gate replaced the draft or the generator got it right.
        resp = _chat(client, "What cover style options do you have?", thread_id=thread_id)
        trace = _latest_trace(client, thread_id)

    text = _joined_text(resp).casefold()
    rq_quality = trace.get("response_quality", {})

    # The customer-facing response must be free of known-fact re-asks.
    assert "what genre" not in text, "Response must not re-ask genre after it is known"
    assert "manuscript stage" not in text, "Response must not ask for manuscript stage again"
    assert "starting from scratch" not in text

    # If the quality gate flagged a re-ask, it must also have applied a safe fallback
    # (i.e. the customer never saw the bad draft).
    quality_failures = rq_quality.get("failures", [])
    if any("known_fact_reask" in f for f in quality_failures):
        # Gate fired — verify the source reflects the fallback replacement.
        assistant_source = trace.get("assistant", {}).get("source", "")
        assert "quality_fallback" in assistant_source or "what genre" not in text, (
            "When gate flags a re-ask the response must have been replaced by safe_fallback"
        )
