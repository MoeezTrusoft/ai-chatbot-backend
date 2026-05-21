"""Integration tests for PR 4: safety, metadata, final readiness."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.components.response.schemas import ResponseDraft
from bookcraft.infra.config import Settings


def _chat(
    client: TestClient,
    message: str,
    *,
    thread_id: object | None = None,
) -> dict[str, Any]:
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


class _FakeGenerator:
    def __init__(self, text: str = "") -> None:
        self._text = text or "Happy to help. What can I assist you with today?"

    async def generate(self, **_kwargs: Any) -> ResponseDraft:
        return ResponseDraft(text=self._text, source="claude_sonnet")

    async def repair(self, *, bad_text: str, **_kwargs: Any) -> ResponseDraft:
        return ResponseDraft(text=bad_text, source="template_no_adapter_repair_unavailable")


# ---------------------------------------------------------------------------
# Test 1 — Directed abuse blocks without assistant response
# ---------------------------------------------------------------------------


def test_directed_abuse_blocks_without_assistant_response() -> None:
    """Directed insult must block — no Claude, no bubbles, system_message returned."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        body = _chat(client, "You are fucking stupid.")

    assert body.get("blocked") is True
    assert body.get("bubbles") == []
    assert body.get("system_message") is not None
    assert len(body.get("system_message", "")) > 10


def test_threat_blocks_with_input_disabled() -> None:
    """Physical threat must block and disable input."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        body = _chat(client, "I will hurt your team.")

    assert body.get("blocked") is True
    assert body.get("input_disabled") is True


# ---------------------------------------------------------------------------
# Test 2 — Casual profanity frustration not blocked
# ---------------------------------------------------------------------------


def test_casual_profanity_frustration_not_blocked() -> None:
    """Situational frustration must not be blocked."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = _FakeGenerator(
            "I understand it can be frustrating. Let me clarify how pricing works."
        )
        body = _chat(client, "This is fucking confusing, I don't understand the pricing.")

    assert body.get("blocked") is not True
    assert len(body.get("bubbles", [])) > 0


# ---------------------------------------------------------------------------
# Test 3 — Publishing platforms saved to context
# ---------------------------------------------------------------------------


def test_publishing_platforms_saved_to_context() -> None:
    """Amazon KDP and IngramSpark mentions should be saved to state."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = _FakeGenerator(
            "Great — Amazon KDP and IngramSpark are both excellent platforms."
        )
        body = _chat(client, "I want to publish on Amazon KDP and IngramSpark.")
        t = _trace(client, body["thread_id"])

    sme = t.get("service_metadata_extraction") or {}
    platforms = sme.get("publishing_platforms") or []
    assert "amazon_kdp" in platforms
    assert "ingramspark" in platforms


# ---------------------------------------------------------------------------
# Test 4 — Book formats saved to context
# ---------------------------------------------------------------------------


def test_book_formats_saved_to_context() -> None:
    """Ebook and paperback formats should be captured."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = _FakeGenerator(
            "Noted — ebook and paperback formats. We can handle both."
        )
        body = _chat(client, "I need it as an ebook and paperback.")
        t = _trace(client, body["thread_id"])

    sme = t.get("service_metadata_extraction") or {}
    formats = sme.get("book_formats") or []
    assert "ebook" in formats
    assert "paperback" in formats


# ---------------------------------------------------------------------------
# Test 5 — Service metadata saved to context
# ---------------------------------------------------------------------------


def test_service_metadata_saved_to_context() -> None:
    """Developmental editing level should be saved."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = _FakeGenerator(
            "Developmental editing is a great choice for a manuscript at this stage."
        )
        body = _chat(client, "I need developmental editing for my novel.")
        t = _trace(client, body["thread_id"])

    sme = t.get("service_metadata_extraction") or {}
    confirmed = sme.get("confirmed") or {}
    editing = confirmed.get("editing_proofreading") or {}
    assert editing.get("editing_level") == "developmental_editing"


# ---------------------------------------------------------------------------
# Test 6 — Negated platform not saved
# ---------------------------------------------------------------------------


def test_negated_platform_not_saved() -> None:
    """Explicitly negated platforms must not end up in confirmed platforms."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = _FakeGenerator(
            "Understood — IngramSpark only, skipping Amazon KDP."
        )
        body = _chat(client, "I don't want Amazon KDP, only IngramSpark.")
        t = _trace(client, body["thread_id"])

    sme = t.get("service_metadata_extraction") or {}
    platforms = sme.get("publishing_platforms") or []
    assert "amazon_kdp" not in platforms
    assert "ingramspark" in platforms


# ---------------------------------------------------------------------------
# Test 7 — Contact then call time progresses consultation
# ---------------------------------------------------------------------------


def test_contact_then_call_time_progresses_consultation() -> None:
    """After contact is captured, the next turn should progress to call time."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        r1 = _chat(client, "I need ghostwriting for my memoir.")
        tid = r1["thread_id"]

        _chat(
            client,
            "My name is Sarah Khan and my email is sarah@example.com",
            thread_id=tid,
        )

        _chat(client, "What happens next?", thread_id=tid)
        t = _trace(client, tid)

    co = t.get("consultation_objective") or {}
    rp = t.get("response_plan") or {}
    # After lead created, should move toward call time.
    assert (
        co.get("ask_preferred_time") is True
        or rp.get("next_question") == "preferred_call_time"
        or rp.get("primary_goal")
        in {"consultation_time_capture", "consultation_handoff_confirmation"}
    )


# ---------------------------------------------------------------------------
# Test 8 — No both email and phone demand
# ---------------------------------------------------------------------------


def test_no_both_email_and_phone_demand() -> None:
    """The bot must never demand both email AND phone."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = _FakeGenerator(
            "What's the best name and email or phone number to reach you?"
        )
        body = _chat(client, "I need help publishing my book.")
        t = _trace(client, body["thread_id"])

    quality = t.get("response_quality") or {}
    failures = quality.get("failures") or []
    assert "demands_both_email_and_phone" not in failures

    text = _text(body).lower()
    assert "email and phone" not in text
