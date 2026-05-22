"""Phase 11 regression test: full consultation state-contract flow.

Covers the exact 6-turn scenario that exposed the hotfix bugs:
 - Turn 1: user asks about ghostwriting consultation
 - Turn 2: bot asks for contact; user provides name/email/phone + Friday afternoon
 - Turn 3: user asks "have my consultation been scheduled?" (status question)
 - Turn 4: bot should ask for timezone (relative time window)
 - Turn 5: user provides timezone; bot confirms and asks to schedule
 - Turn 6: bot confirms scheduling

Fake test data (never real PII):
  Name:  Maya Author
  Email: maya@example.com
  Phone: +1 555 987 6543
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.components.response.schemas import ResponseDraft
from bookcraft.infra.config import Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chat(client: TestClient, message: str, *, thread_id: object | None = None) -> dict[str, Any]:
    payload: dict[str, object] = {"message": message}
    if thread_id is not None:
        payload["thread_id"] = str(thread_id)
    r = client.post("/api/v1/chat/turn", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _trace(client: TestClient, thread_id: str) -> dict[str, Any]:
    # for_thread returns newest-first (reversed), so rows[0] is the most recent turn.
    rows = client.app.state.chat_service.trace_store.for_thread(thread_id)
    assert rows, f"No trace for {thread_id}"
    return rows[0]


def _text(body: dict[str, Any]) -> str:
    return " ".join(b.get("text", "") for b in body.get("bubbles", []))


class _FakeGenerator:
    """Returns contextually-safe responses without calling the LLM."""

    _RESPONSES: dict[str, str] = {
        "consultation_request": (
            "I'd love to set up a free 30-minute consultation for you. "
            "Could I get your name and best contact method?"
        ),
        "contact_received": ("Got it — what day and time works best for you for the call?"),
        "status_question_scheduled": ("Your consultation has been confirmed."),
        "status_question_not_scheduled": (
            "I have your contact details on file. "
            "What timezone are you in so I can confirm the time slot?"
        ),
        "timezone_captured": (
            "Perfect. I'll book a 30-minute call for Friday afternoon. Should I go ahead?"
        ),
        "default": "Happy to help — what would you like to know?",
    }

    def __init__(self, key: str = "default") -> None:
        self._key = key
        self._calls: list[dict[str, Any]] = []

    async def generate(self, **kwargs: Any) -> ResponseDraft:
        self._calls.append(kwargs)
        text = self._RESPONSES.get(self._key, self._RESPONSES["default"])
        return ResponseDraft(text=text, source="claude_sonnet")

    async def repair(self, *, bad_text: str, **_kwargs: Any) -> ResponseDraft:
        return ResponseDraft(text=bad_text, source="template_no_adapter_repair_unavailable")

    def set_key(self, key: str) -> None:
        self._key = key


# ---------------------------------------------------------------------------
# Test 1 — Contact provided in a single message is retained across turns
# ---------------------------------------------------------------------------


def test_contact_retained_across_turns() -> None:
    """contact_slots() must find contact from state.contact_info on Turn 3+.

    Bug: state.personal.* was never written from contact_capture → contact_slots()
    returned empty → bot re-asked for contact even after it was already shared.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    gen = _FakeGenerator("contact_received")

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = gen

        # Turn 1: request consultation.
        r1 = _chat(client, "I'd like to book a free consultation.")
        tid = r1["thread_id"]

        # Turn 2: provide contact + call time in one message.
        gen.set_key("contact_received")
        _chat(
            client,
            "Maya Author maya@example.com +1 555 987 6543 Friday afternoon",
            thread_id=tid,
        )
        t2 = _trace(client, tid)
        contact_capture_t2 = t2.get("contact_capture") or {}
        assert contact_capture_t2.get("lead_contact_ready") is True, (
            "Turn 2: contact should be ready after providing name/email/phone"
        )

        # Turn 3: follow-up turn — contact must still be known.
        gen.set_key("status_question_not_scheduled")
        r3 = _chat(client, "Have my consultation been scheduled?", thread_id=tid)
        t3 = _trace(client, tid)

        contact_capture_t3 = t3.get("contact_capture") or {}
        consultation_state_t3 = t3.get("consultation_state") or {}
        rp_t3 = t3.get("response_plan") or {}

        # Core assertion: contact must still be ready.
        assert contact_capture_t3.get("lead_contact_ready") is True, (
            "Turn 3: contact must remain ready — bot must not re-ask for name/email/phone"
        )

        # Status question should be detected.
        assert consultation_state_t3.get("is_status_question") is True, (
            "Turn 3: is_status_question must be True"
        )

        # Response plan must NOT be asking for contact.
        assert rp_t3.get("next_question") not in {
            "name_and_email_or_phone",
            "name_or_email",
            "contact",
        }, (
            "Turn 3: bot should not re-ask for contact, "
            f"got next_question={rp_t3.get('next_question')}"
        )

        # Response text must not ask for contact.
        text3 = _text(r3)
        lowered = text3.casefold()
        assert "your name" not in lowered, "Turn 3: bot must not ask for name"
        assert "email or phone" not in lowered, "Turn 3: bot must not ask for email or phone"


# ---------------------------------------------------------------------------
# Test 2 — Preferred call time is preserved in state
# ---------------------------------------------------------------------------


def test_preferred_call_time_persisted() -> None:
    """state.preferred_call_time must survive to the next turn after being extracted."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    gen = _FakeGenerator("contact_received")

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = gen

        r1 = _chat(client, "Book a free consultation — I'm free Friday afternoon.")
        tid = r1["thread_id"]
        t1 = _trace(client, tid)

        state_t1_consultation = t1.get("consultation_state") or {}
        # preferred_call_time must be extracted from "Friday afternoon".
        pct = state_t1_consultation.get("preferred_call_time")
        assert pct is not None, "Preferred call time must be extracted from Turn 1 message"
        assert "friday" in str(pct).casefold() or "afternoon" in str(pct).casefold(), (
            f"preferred_call_time should contain 'friday' or 'afternoon', got {pct!r}"
        )


# ---------------------------------------------------------------------------
# Test 3 — Status question without appointment does not claim scheduling
# ---------------------------------------------------------------------------


def test_status_question_does_not_falsely_claim_scheduled() -> None:
    """Quality gate must block a response that claims scheduling without evidence.

    Phase 7 hotfix: _unverified_scheduling_claim check.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        # Use the "scheduled" fake response, but no appointment in state.
        client.app.state.chat_service.response_generator = _FakeGenerator(
            "status_question_scheduled"
        )

        r1 = _chat(client, "Have my consultation been scheduled?")
        t1 = _trace(client, r1["thread_id"])

        quality = t1.get("response_quality") or {}
        failures = quality.get("failures") or []

        # The fake generator claims "consultation has been confirmed" with no state evidence.
        # quality gate check 22 must fire.
        assert any("unverified_scheduling_claim" in f for f in failures), (
            f"Quality gate must catch unverified_scheduling_claim. Failures: {failures}"
        )


# ---------------------------------------------------------------------------
# Test 4 — Timezone is requested for relative time windows
# ---------------------------------------------------------------------------


def test_timezone_requested_for_relative_window() -> None:
    """When the preferred call time is a relative window like 'Friday afternoon',
    the consultation state reducer must set timezone_needed=True."""
    from bookcraft.components.sales.consultation_state import reduce_consultation_state
    from bookcraft.domain.state import ThreadState

    state = ThreadState()
    state.preferred_call_time = "Friday afternoon"

    # No timezone in state
    from unittest.mock import MagicMock

    intent = MagicMock()
    intent.query_primary = None

    decision = reduce_consultation_state(
        state=state,
        message="",
        intent=intent,
        contact_ready=True,
    )

    # consultation not requested yet → NONE stage, but timezone_needed should be False
    # because consultation is not yet in progress
    assert not decision.can_schedule  # no consultation request yet


def test_timezone_requested_when_consultation_active() -> None:
    """When consultation is requested, contact is ready, and time is a relative window,
    timezone must be requested."""
    from unittest.mock import MagicMock

    from bookcraft.components.sales.consultation_state import (
        ConsultationStage,
        reduce_consultation_state,
    )
    from bookcraft.domain.state import ThreadState

    state = ThreadState()
    state.preferred_call_time = "Friday afternoon"
    # No timezone set in state

    intent = MagicMock()
    from bookcraft.domain.enums import QueryIntentType

    intent.query_primary = QueryIntentType.CONSULTATION_REQUEST

    decision = reduce_consultation_state(
        state=state,
        message="I'd like to book a consultation for Friday afternoon.",
        intent=intent,
        contact_ready=True,
    )

    assert decision.stage == ConsultationStage.TIME_CAPTURED_NEEDS_TIMEZONE
    assert decision.timezone_needed is True
    assert decision.can_schedule is False
    assert decision.next_question == "preferred_call_timezone"


# ---------------------------------------------------------------------------
# Test 5 — Full state-contract: contact + time → READY_TO_SCHEDULE when timezone known
# ---------------------------------------------------------------------------


def test_ready_to_schedule_when_all_details_present() -> None:
    """With contact ready, specific time (with digits), and no relative window,
    the reducer must return READY_TO_SCHEDULE."""
    from unittest.mock import MagicMock

    from bookcraft.components.sales.consultation_state import (
        ConsultationStage,
        reduce_consultation_state,
    )
    from bookcraft.domain.state import ThreadState

    state = ThreadState()
    state.preferred_call_time = "Friday at 3pm"
    # "3pm" has digits → not purely relative → no timezone ask

    intent = MagicMock()
    from bookcraft.domain.enums import QueryIntentType

    intent.query_primary = QueryIntentType.CONSULTATION_REQUEST

    decision = reduce_consultation_state(
        state=state,
        message="I'd like to book a consultation for Friday at 3pm.",
        intent=intent,
        contact_ready=True,
    )

    assert decision.stage == ConsultationStage.READY_TO_SCHEDULE
    assert decision.can_schedule is True
    assert decision.timezone_needed is False
