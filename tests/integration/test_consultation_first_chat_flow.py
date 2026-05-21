"""Integration tests for PR 2: Consultation-First Sales Planner."""

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


class _FakeGenerator:
    """Always returns a clean, contextually appropriate response."""

    def __init__(self, text: str = "") -> None:
        self._text = text

    async def generate(self, **kwargs: Any) -> ResponseDraft:
        # Use provided text or construct a safe default.
        text = self._text or "Happy to help. What can I assist you with today?"
        return ResponseDraft(text=text, source="claude_sonnet")

    async def repair(self, *, bad_text: str, **_kwargs: Any) -> ResponseDraft:
        return ResponseDraft(text=bad_text, source="template_no_adapter_repair_unavailable")


# ---------------------------------------------------------------------------
# Test 1 — Pricing question is answered before contact capture
# ---------------------------------------------------------------------------


def test_pricing_question_answers_before_contact_capture() -> None:
    """When user asks 'how much does ghostwriting cost?', the engine should not
    immediately ask for contact — it should flag answer_before_capture."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = _FakeGenerator(
            "Ghostwriting pricing depends on scope — word count, research depth, and revision "
            "rounds. For a rough idea, most full-length manuscripts are scoped by word count. "
            "Would you like to connect with a specialist who can give you a personalised estimate?"
        )
        body = _chat(client, "How much does ghostwriting cost?")
        t = _trace(client, body["thread_id"])

    cqp = t.get("current_question_priority") or {}
    abc = t.get("answer_before_capture") or {}
    rp = t.get("response_plan") or {}

    assert cqp.get("has_priority") is True
    assert cqp.get("question_type") == "pricing"
    assert abc.get("should_answer_first") is True
    assert abc.get("suppress_contact_until_answered") is True
    assert rp.get("primary_goal") == "answer_current_question"


# ---------------------------------------------------------------------------
# Test 2 — Contact provided → ask for preferred call time
# ---------------------------------------------------------------------------


def test_contact_provided_then_asks_best_call_time() -> None:
    """After contact is captured and lead confirmed, the bot asks for preferred call time."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        # Turn 1: express interest.
        r1 = _chat(client, "I need ghostwriting help for my memoir.")
        tid = r1["thread_id"]

        # Turn 2: provide contact → lead created and confirmed.
        _chat(
            client,
            "My name is Sarah Khan and my email is sarah@example.com",
            thread_id=tid,
        )

        # Turn 3: follow-up → engine detects lead_created from prior turn, asks call time.
        client.app.state.chat_service.response_generator = _FakeGenerator(
            "What's the best time to reach you — morning, afternoon, or evening?"
        )
        _chat(client, "Great, what happens next?", thread_id=tid)
        t = _trace(client, tid)

    co = t.get("consultation_objective") or {}
    rp = t.get("response_plan") or {}

    assert t.get("lead_created") is True
    assert co.get("ask_preferred_time") is True or rp.get("next_question") == "preferred_call_time"


# ---------------------------------------------------------------------------
# Test 3 — Call time after contact → moves to consultation pending
# ---------------------------------------------------------------------------


def test_call_time_after_contact_moves_to_consultation_pending() -> None:
    """After contact + call time captured, stage should be consultation_pending."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        r1 = _chat(client, "I need editing for my completed novel.")
        tid = r1["thread_id"]

        _chat(
            client,
            "My name is Sarah Khan and my email is sarah@example.com",
            thread_id=tid,
        )

        client.app.state.chat_service.response_generator = _FakeResponseGeneratorWithTime()
        _chat(client, "Friday afternoon works best for me.", thread_id=tid)
        t = _trace(client, tid)

    co = t.get("consultation_objective") or {}
    rp = t.get("response_plan") or {}
    # Either consultation_pending stage OR handoff goal is reached.
    stage = co.get("stage", "")
    goal = rp.get("primary_goal", "")
    assert stage in {"consultation_pending", "consultation_time_requested"} or goal in {
        "consultation_handoff_confirmation",
        "consultation_time_capture",
    }


# ---------------------------------------------------------------------------
# Test 4 — Distribution question overrides old ghostwriting path
# ---------------------------------------------------------------------------


def test_distribution_question_overrides_old_ghostwriting_path() -> None:
    """If user corrects the bot to distribution, engine should answer distribution first."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        r1 = _chat(client, "There's a story I'd like written.")
        tid = r1["thread_id"]

        client.app.state.chat_service.response_generator = _FakeGenerator(
            "For distribution, BookCraft supports publishing setup on Amazon KDP, IngramSpark, "
            "and other major platforms. Would you like to connect with a specialist?"
        )
        body = _chat(client, "I was asking about distribution, not ghostwriting.", thread_id=tid)
        t = _trace(client, body["thread_id"])

    cqp = t.get("current_question_priority") or {}
    assert cqp.get("has_priority") is True
    assert cqp.get("question_type") in {"topic_correction", "distribution"}
    assert (
        cqp.get("suppress_old_sales_path") is True
        or cqp.get("should_answer_before_capture") is True
    )


# ---------------------------------------------------------------------------
# Test 5 — Unsure user gets options, not vague question
# ---------------------------------------------------------------------------


def test_unsure_user_gets_options() -> None:
    """Greeting or unsure start should not ask word count or genre immediately."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        body = _chat(client, "I'm not sure where to start with my book project.")
        t = _trace(client, body["thread_id"])

    rp = t.get("response_plan") or {}
    # Should not immediately ask for genre/word_count — should ask how we can help or offer options.
    nq = rp.get("next_question", "")
    assert nq not in {"word_or_page_count", "deadline"}


# ---------------------------------------------------------------------------
# Test 6 — No both email and phone demand
# ---------------------------------------------------------------------------


def test_no_both_email_and_phone_demand() -> None:
    """The bot must never demand both email and phone simultaneously."""
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

    # Also verify the response text does not demand both.
    text = _text(body).lower()
    assert "email and phone" not in text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeResponseGeneratorWithTime:
    """Returns a response that captures call time preference."""

    async def generate(self, **kwargs: Any) -> ResponseDraft:
        return ResponseDraft(
            text=(
                "Friday afternoon is noted. A BookCraft specialist will reach out then. "
                "We look forward to speaking with you!"
            ),
            source="claude_sonnet",
        )

    async def repair(self, *, bad_text: str, **_kwargs: Any) -> ResponseDraft:
        return ResponseDraft(text=bad_text, source="template_no_adapter_repair_unavailable")
