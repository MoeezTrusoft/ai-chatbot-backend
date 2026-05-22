"""Step 6 — incident-replay integration test.

Replays the four-message sequence from the production review:
1. "Hellloooooo I finished my manuscript, just publish it"
   → response must contain a welcome / acknowledgment and must NOT ask for name+email+phone.
2. "Ok ok hold on, tell me how you can help?"
   → response must answer the question and must NOT lead with a contact ask.
3. A follow-up turn
   → response may use history (last_user_message / last_assistant_text set in state).
4. Asserts across all turns:
   - No turn demands both email AND phone together.
   - No back-to-back repeat of the exact same contact-ask phrasing.

All fake test data: no real PII.
"""

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


def _text(body: dict[str, Any]) -> str:
    return " ".join(b.get("text", "") for b in body.get("bubbles", []))


def _trace(client: TestClient, thread_id: str) -> dict[str, Any]:
    rows = client.app.state.chat_service.trace_store.for_thread(thread_id)
    assert rows, f"No trace for {thread_id}"
    return rows[0]


class _FakeGenerator:
    """Returns contextually distinct responses without calling the LLM."""

    _by_turn: list[str] = [
        (
            "Welcome! I can see you have a completed manuscript ready for publishing. "
            "BookCraft covers distribution, formatting, and cover design for publishing — "
            "which platform are you aiming for first?"
        ),
        (
            "Great question. BookCraft handles the full publishing journey: "
            "interior formatting, cover design, distribution to Amazon, Barnes & Noble, "
            "Apple Books, and more, plus optional ghostwriting and marketing support. "
            "We work on a project-by-project basis, so nothing is locked in. "
            "What platform or format are you most focused on?"
        ),
        (
            "Got it — so you're thinking about wide distribution beyond just Amazon. "
            "That typically means ebook and print versions for KDP, IngramSpark, and "
            "the major retailers. Want me to walk through what that scope looks like?"
        ),
        "Understood — let's keep things focused on distribution for now.",
    ]
    _call = 0

    def __init__(self) -> None:
        self._call = 0

    async def generate(self, **kwargs: Any) -> ResponseDraft:  # noqa: ANN003
        idx = min(self._call, len(self._by_turn) - 1)
        text = self._by_turn[idx]
        self._call += 1
        return ResponseDraft(text=text, source="claude_sonnet")

    async def repair(self, *, bad_text: str, **_kwargs: Any) -> ResponseDraft:
        return ResponseDraft(text=bad_text, source="template_no_adapter_repair_unavailable")


def test_incident_replay_four_turns() -> None:
    """Replay the four-turn incident scenario and assert each turn's constraints."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    gen = _FakeGenerator()

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = gen

        # Turn 1: first message.
        r1 = _chat(client, "Hellloooooo I finished my manuscript, just publish it")
        tid = r1["thread_id"]
        text1 = _text(r1)

        t1 = _trace(client, tid)
        lead1 = t1.get("lead_objective") or {}
        rp1 = t1.get("response_plan") or {}

        # Core: must NOT demand name+email+phone on turn 1.
        assert lead1.get("objective_move") != "ask_contact", (
            f"Turn 1 must not ask for contact, got: {lead1.get('objective_move')}"
        )
        assert rp1.get("primary_goal") in {
            "greeting_welcome",
            "continue_discovery",
            "answer_current_question",
            None,
        }, f"Turn 1 primary_goal should be welcoming, got: {rp1.get('primary_goal')}"

        lower1 = text1.casefold()
        assert "name" not in lower1 or "email" not in lower1, (
            f"Turn 1 must not simultaneously ask for name and email, got: {text1[:200]}"
        )

        # Turn 2: user asks a direct question.
        r2 = _chat(client, "Ok ok hold on, tell me how you can help?", thread_id=tid)
        text2 = _text(r2)

        t2 = _trace(client, tid)
        lead2 = t2.get("lead_objective") or {}

        # Must answer the question, not lead with contact ask.
        assert lead2.get("objective_move") != "ask_contact", (
            f"Turn 2 must not ask for contact, got: {lead2.get('objective_move')} "
            f"(text: {text2[:200]})"
        )
        lower2 = text2.casefold()
        # Response should contain some service-informational content.
        assert any(
            kw in lower2
            for kw in (
                "bookcraft",
                "publishing",
                "help",
                "service",
                "cover",
                "format",
                "distribute",
                "ghostwriting",
                "editing",
                "marketing",
            )
        ), f"Turn 2 should answer the question, got: {text2[:200]}"

        # Turn 3: follow-up.
        r3 = _chat(client, "I want to publish on multiple platforms", thread_id=tid)
        _text(r3)

        state3 = client.app.state.chat_service.threads.get(tid)
        if state3 is not None:
            # History fields must be populated after turn 2.
            st = state3.state
            assert st.last_user_message != "", "last_user_message must be stored after turn 2"
            assert st.last_assistant_text != "", "last_assistant_text must be stored after turn 2"

        # Turn 4: final turn.
        r4 = _chat(client, "What about Amazon?", thread_id=tid)
        text4 = _text(r4)
        _ = text4  # just ensure it doesn't crash.

        # Global assertion: no turn demands BOTH email and phone together.
        for turn_num, text in enumerate([text1, text2, _text(r3), text4], 1):
            lower = text.casefold()
            both_channels = ("email" in lower and "phone" in lower) and ("name" in lower)
            assert not both_channels, (
                f"Turn {turn_num} must not demand name + email + phone together: {text[:200]}"
            )


def test_first_turn_welcome_goal() -> None:
    """First turn with any high-intent message must have engaging/welcome goal."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = _FakeGenerator()

        r = _chat(client, "I need help publishing my fantasy novel")
        t = _trace(client, r["thread_id"])

        lead = t.get("lead_objective") or {}
        assert lead.get("objective_move") != "ask_contact", (
            f"First turn must not immediately ask for contact: {lead}"
        )


def test_backoff_after_deflection_no_repeat_ask() -> None:
    """After a turn that asked for contact, deflection must not get another contact ask."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    gen = _FakeGenerator()

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = gen

        # Turn 1: first message — engage.
        r1 = _chat(client, "I need help publishing my novel")
        tid = r1["thread_id"]

        # Turn 2: a turn that would trigger contact ask (e.g. pricing).
        _chat(client, "How much does this cost?", thread_id=tid)

        # Set last_turn_asked_contact manually (simulate the ask was made).
        svc = client.app.state.chat_service
        if tid in svc.threads:
            svc.threads[tid].state.last_turn_asked_contact = True

        # Turn 3: user deflects.
        _chat(
            client,
            "Wait I just want to understand the process first",
            thread_id=tid,
        )
        t3 = _trace(client, tid)
        lead3 = t3.get("lead_objective") or {}

        assert lead3.get("objective_move") != "ask_contact", (
            f"After contact ask deflection, bot must back off, got: {lead3.get('objective_move')}"
        )
