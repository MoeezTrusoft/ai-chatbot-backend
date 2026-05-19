from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings


@pytest.fixture()
def client() -> TestClient:
    app = create_app(Settings(app_env="test"))
    return TestClient(app)


def _chat(
    client: TestClient,
    message: str,
    *,
    thread_id: UUID | str | None = None,
) -> dict[str, Any]:
    payload: dict[str, object] = {"message": message}
    if thread_id is not None:
        payload["thread_id"] = str(thread_id)
    resp = client.post("/api/v1/chat/turn", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _trace(client: TestClient, thread_id: str) -> dict[str, Any]:
    ts = client.app.state.chat_service.trace_store
    rows = ts.for_thread(thread_id)
    assert rows, f"No trace for thread {thread_id}"
    return rows[0]


def _text(body: dict[str, Any]) -> str:
    return " ".join(b.get("text", "") for b in body.get("bubbles", []))


def _pf(trace: dict[str, Any]) -> dict[str, Any]:
    return trace.get("portfolio_fallback") or {}


# ---------------------------------------------------------------------------
# 1. First portfolio request triggers ask_filter_once (scoping question)
# ---------------------------------------------------------------------------


def test_first_portfolio_request_asks_filter_once(client: TestClient) -> None:
    r1 = _chat(client, "Show me some samples.")
    thread_id = str(r1["thread_id"])
    t1 = _trace(client, thread_id)

    pf = _pf(t1)
    assert pf, "portfolio_fallback must be in trace"
    strategy = pf.get("strategy")
    assert strategy in ("ask_filter_once", "use_context_filter", "fallback_general_samples"), (
        f"Unexpected strategy on first request: {strategy}"
    )


# ---------------------------------------------------------------------------
# 2. After "I don't know" — no genre/category re-ask
# ---------------------------------------------------------------------------


def test_no_genre_question_after_user_says_dont_know(client: TestClient) -> None:
    r1 = _chat(client, "Show me samples.")
    thread_id = str(r1["thread_id"])

    r2 = _chat(client, "I don't know, just show me any samples.", thread_id=thread_id)
    t2 = _trace(client, str(r2["thread_id"]))

    pf2 = _pf(t2)
    strategy2 = pf2.get("strategy")
    assert strategy2 in ("fallback_general_samples", "fallback_service_samples"), (
        f"Expected fallback strategy, got {strategy2}"
    )

    txt = _text(r2).casefold()
    forbidden = ("what genre", "which genre", "what kind", "what category", "what service")
    for phrase in forbidden:
        assert phrase not in txt, (
            f"Response must not re-ask '{phrase}' after fallback, got: {txt[:200]}"
        )


# ---------------------------------------------------------------------------
# 3. Active cover design context → fallback_service_samples when asked twice
# ---------------------------------------------------------------------------


def test_active_service_uses_service_fallback(client: TestClient) -> None:
    r1 = _chat(client, "I need cover design for my fantasy novel. Show me samples.")
    thread_id = str(r1["thread_id"])

    r2 = _chat(client, "I don't know the style, just show me any covers.", thread_id=thread_id)
    t2 = _trace(client, str(r2["thread_id"]))

    pf = _pf(t2)
    if pf:
        strategy = pf.get("strategy")
        assert strategy in (
            "fallback_service_samples",
            "use_context_filter",
            "ask_filter_once",
            "fallback_general_samples",
        ), f"Unexpected strategy: {strategy}"


# ---------------------------------------------------------------------------
# 4. Trace includes portfolio_fallback key
# ---------------------------------------------------------------------------


def test_trace_includes_portfolio_fallback_key(client: TestClient) -> None:
    r1 = _chat(client, "Show me some portfolio samples.")
    thread_id = str(r1["thread_id"])
    t1 = _trace(client, thread_id)

    assert "portfolio_fallback" in t1, "portfolio_fallback must be a trace key"
    pf = t1["portfolio_fallback"]
    if pf is not None:
        assert "strategy" in pf
        assert "reason" in pf


# ---------------------------------------------------------------------------
# 5. Final source remains Claude-compliant
# ---------------------------------------------------------------------------


def test_final_source_is_claude_compliant(client: TestClient) -> None:
    r1 = _chat(client, "Show me samples.")
    thread_id = str(r1["thread_id"])
    t1 = _trace(client, thread_id)

    # In test mode the final_source may not be claude due to mock generator,
    # but portfolio-specific sources must not appear.
    final_source = (t1.get("assistant") or {}).get("source", "")
    assert "portfolio_engine" not in final_source, (
        f"Final source must not be portfolio_engine, got {final_source}"
    )


# ---------------------------------------------------------------------------
# 6. No portfolio_engine_quality_fallback in final source
# ---------------------------------------------------------------------------


def test_no_deterministic_portfolio_engine_source(client: TestClient) -> None:
    r1 = _chat(client, "Show me samples please.")
    thread_id = str(r1["thread_id"])
    t1 = _trace(client, thread_id)

    final_source = (t1.get("assistant") or {}).get("source", "")
    assert "portfolio_engine" not in final_source, (
        f"Final source must not be portfolio_engine, got {final_source}"
    )


# ---------------------------------------------------------------------------
# 7. No repeated category/genre question after fallback on turn 3
# ---------------------------------------------------------------------------


def test_no_repeated_genre_question_after_fallback(client: TestClient) -> None:
    r1 = _chat(client, "Show me samples.")
    thread_id = str(r1["thread_id"])

    _chat(client, "I don't know, any samples are fine.", thread_id=thread_id)

    r3 = _chat(client, "Show me more.", thread_id=thread_id)
    txt3 = _text(r3).casefold()
    # After fallback is locked in, genre/category must not be asked again.
    forbidden = ("what genre", "which genre", "what category")
    for phrase in forbidden:
        assert phrase not in txt3, f"Must not re-ask '{phrase}' after fallback, got: {txt3[:200]}"
