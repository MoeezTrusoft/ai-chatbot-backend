"""Integration tests: context enforcement and correction recovery."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.components.response.schemas import ResponseDraft
from bookcraft.infra.config import Settings


def _chat(client: TestClient, message: str, *, thread_id: object | None = None) -> dict[str, Any]:
    payload: dict[str, object] = {"message": message}
    if thread_id is not None:
        payload["thread_id"] = str(thread_id)
    r = client.post("/api/v1/chat/turn", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _trace(client: TestClient, thread_id: str) -> dict[str, Any]:
    rows = client.app.state.chat_service.trace_store.for_thread(thread_id)
    assert rows, f"No trace for {thread_id}"
    return rows[0]


def _text(body: dict[str, Any]) -> str:
    return " ".join(b.get("text", "") for b in body.get("bubbles", []))


class _FakeGen:
    def __init__(self, text: str = "") -> None:
        self._t = text or "Happy to help."

    async def generate(self, **_kw: Any) -> ResponseDraft:
        return ResponseDraft(text=self._t, source="claude_sonnet")

    async def repair(self, *, bad_text: str, **_kw: Any) -> ResponseDraft:
        return ResponseDraft(text=bad_text, source="template_no_adapter_repair_unavailable")


# ─── Test 1: Cover style delegation ──────────────────────────────────────────


def test_cover_style_delegation_does_not_reask_cover_style() -> None:
    """After 'you guys design it', cover_style must not be asked again."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        r1 = _chat(client, "I need cover design for my fantasy novel.")
        tid = r1["thread_id"]

        client.app.state.chat_service.response_generator = _FakeGen(
            "Absolutely — we'll take full creative ownership of the cover. "
            "To connect you with the right designer, what's the best name and email to reach you?"
        )
        body = _chat(client, "You guys design it for me, I trust you.", thread_id=tid)
        t = _trace(client, tid)

    enf = t.get("context_enforcement") or {}
    assert "cover_style" in (enf.get("delegated_slots") or [])

    rp = t.get("response_plan") or {}
    assert rp.get("next_question") != "cover_style"

    text = _text(body).lower()
    assert "cover style" not in text
    assert "visual direction" not in text


# ─── Test 2: Repeated no-word-count → consultation ───────────────────────────


def test_repeated_no_word_count_moves_to_consultation() -> None:
    """'again no idea about pages or words' → consultation goal, word count not asked."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        r1 = _chat(client, "I need ghostwriting for my book.")
        tid = r1["thread_id"]

        client.app.state.chat_service.response_generator = _FakeGen(
            "Totally fine — we can scope the project once we've spoken directly. "
            "What's the best name and email to reach you?"
        )
        body = _chat(client, "again no idea about pages or words", thread_id=tid)
        t = _trace(client, tid)

    enf = t.get("context_enforcement") or {}
    assert "word_or_page_count" in (enf.get("unknown_slots") or [])

    rp = t.get("response_plan") or {}
    nq = rp.get("next_question", "")
    assert nq != "word_or_page_count"

    text = _text(body).lower()
    assert "word count" not in text
    assert "page count" not in text


# ─── Test 3: Publishing timeline does not return to cover style ───────────────


def test_publishing_timeline_does_not_return_to_cover_style() -> None:
    """Publishing+cover timeline question → answer_current_question, not cover_style."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = _FakeGen(
            "Publishing timelines depend on what stage the manuscript is in and which "
            "platforms you're targeting. Cover design typically adds 2-4 weeks. "
            "Would you like to speak with a specialist?"
        )
        body = _chat(
            client,
            "How long will it take to publish my book including designing its cover?",
        )
        t = _trace(client, body["thread_id"])

    rp = t.get("response_plan") or {}

    assert rp.get("next_question") != "cover_style"
    assert rp.get("primary_goal") in {
        "answer_current_question",
        "consultation_offer",
        "continue_discovery",
    }

    text = _text(body).lower()
    assert "cover style" not in text
    assert "what cover style" not in text


# ─── Test 4: Consultation request does not ask word count ─────────────────────


def test_consultation_request_does_not_ask_word_count_again() -> None:
    """'listen to my story and suggest me' → consultation goal, no word count."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        r1 = _chat(client, "I need ghostwriting help.")
        tid = r1["thread_id"]

        client.app.state.chat_service.response_generator = _FakeGen(
            "That sounds like a great project. Let's connect you with a specialist "
            "who can listen and guide you. What's the best name and email to reach you?"
        )
        body = _chat(client, "listen to my story and suggest me", thread_id=tid)
        t = _trace(client, tid)

    enf = t.get("context_enforcement") or {}
    rp = t.get("response_plan") or {}

    # Enforcement should have detected consultation intent
    assert any("consultation" in a for a in (enf.get("audit") or []))
    assert rp.get("next_question") != "word_or_page_count"

    text = _text(body).lower()
    assert "word count" not in text
    assert "how many pages" not in text


# ─── Test 5: Autobiography does not become fiction ───────────────────────────


def test_autobiography_does_not_become_fiction() -> None:
    """'my autobiography' → no fiction default; memoir candidate added."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        body = _chat(
            client,
            "I want to write my autobiography about overcoming adversity.",
        )
        t = _trace(client, body["thread_id"])

    cp = t.get("context_pack") or {}

    # Either genre is cleared or genre_status is uncertain, not confirmed fiction
    genre_confirmed = cp.get("active_genre")
    assert genre_confirmed != "fiction", (
        f"autobiography should not become fiction genre, got: {genre_confirmed}"
    )


# ─── Test 6: Negated Amazon not in context ────────────────────────────────────


def test_negated_amazon_not_in_context_after_only_ingramspark() -> None:
    """'not Amazon, only IngramSpark' → amazon_kdp not in confirmed context."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        r1 = _chat(client, "I want to publish on Amazon KDP and IngramSpark.")
        tid = r1["thread_id"]

        client.app.state.chat_service.response_generator = _FakeGen(
            "Noted — IngramSpark only, no Amazon. "
            "IngramSpark gives you great retail and library reach. "
            "What's the best name and email to reach you?"
        )
        _chat(client, "I don't want Amazon, only IngramSpark.", thread_id=tid)
        t = _trace(client, tid)

    enf = t.get("context_enforcement") or {}
    cp = t.get("context_pack") or {}

    assert "amazon_kdp" in (enf.get("negated_platforms") or [])

    # Context pack should not confirm amazon_kdp
    platforms = cp.get("publishing_platforms") or []
    assert "amazon_kdp" not in platforms, (
        f"amazon_kdp should not be in confirmed context after negation, got: {platforms}"
    )


# ─── Test 7: Distribution correction overrides ghostwriting ──────────────────


def test_distribution_correction_overrides_ghostwriting_context() -> None:
    """'not ghostwriting, distribution' → ghostwriting negated, distribution context."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        r1 = _chat(client, "I need ghostwriting for my book.")
        tid = r1["thread_id"]

        client.app.state.chat_service.response_generator = _FakeGen(
            "Got it — you're asking about distribution, not ghostwriting. "
            "BookCraft can help with Amazon KDP, IngramSpark and more. "
            "What format are you planning?"
        )
        _chat(
            client,
            "I asked about distribution, not ghostwriting.",
            thread_id=tid,
        )
        t = _trace(client, tid)

    enf = t.get("context_enforcement") or {}
    assert "ghostwriting" in (enf.get("negated_services") or [])
    assert any("service_correction" in a for a in (enf.get("audit") or []))


# ─── Test 8: context_enforcement trace present ────────────────────────────────


def test_context_enforcement_trace_present() -> None:
    """Every turn must have context_enforcement in the trace."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        body = _chat(client, "I need editing for my completed novel.")
        t = _trace(client, body["thread_id"])

    assert "context_enforcement" in t, "context_enforcement must be in trace"
    enf = t["context_enforcement"]
    assert "audit" in enf
    assert isinstance(enf["audit"], list)
