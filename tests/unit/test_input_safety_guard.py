"""Tests for InputSafetyGuard."""

from __future__ import annotations

from bookcraft.components.safety.input_guard import InputSafetyGuard
from bookcraft.domain.state import ThreadState

guard = InputSafetyGuard()


def _state_with_events(events: list[dict]) -> ThreadState:
    s = ThreadState()
    s.safety_events = list(events)
    return s


def _warn_event() -> dict:
    return {
        "action": "warn",
        "severity": 2,
        "reason": "test",
        "message_preview": "x",
        "recorded_at": "2026-01-01T00:00:00+00:00",
    }


def _block_event() -> dict:
    return {
        "action": "block",
        "severity": 3,
        "reason": "test",
        "message_preview": "x",
        "recorded_at": "2026-01-01T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# Allow / warn / block tests
# ---------------------------------------------------------------------------


def test_allows_casual_profanity_frustration() -> None:
    """Frustration directed at a situation, not at the bot/team."""
    decision = guard.evaluate("This is fucking confusing, I don't understand.")
    assert decision.action in {"allow", "warn"}
    assert not decision.input_disabled


def test_allows_casual_what_the_fuck() -> None:
    decision = guard.evaluate("What the fuck, this doesn't make sense.")
    assert decision.action in {"allow", "warn"}


def test_blocks_directed_insult() -> None:
    """Personal insult directed at the bot or team."""
    decision = guard.evaluate("You are fucking stupid.")
    assert decision.action == "block"
    assert decision.system_message is not None


def test_blocks_directed_insult_useless_bot() -> None:
    decision = guard.evaluate("What a useless bot you are.")
    assert decision.action == "block"


def test_blocks_threat() -> None:
    """Physical threat must always block."""
    decision = guard.evaluate("I will hurt your team if this isn't fixed.")
    assert decision.action == "block"
    assert decision.input_disabled is True
    assert decision.system_message is not None


def test_blocks_threat_variant() -> None:
    decision = guard.evaluate("You'll regret this. I know where you are.")
    assert decision.action == "block"


def test_blocks_hate() -> None:
    """Identity-based hate speech must block."""
    decision = guard.evaluate("Go die, you fucking faggot.")
    assert decision.action == "block"
    assert decision.input_disabled is True


def test_allows_normal_customer_complaint() -> None:
    """Legitimate service complaint must not be blocked."""
    decision = guard.evaluate("This service is too expensive for what it offers.")
    assert decision.action == "allow"
    assert not decision.input_disabled


def test_allows_complaint_unhappy() -> None:
    decision = guard.evaluate("I'm unhappy with the response I got.")
    assert decision.action == "allow"


def test_repeated_hostility_blocks() -> None:
    """Three+ warn/block events → escalate to block."""
    state = _state_with_events([_warn_event(), _warn_event(), _warn_event()])
    decision = guard.evaluate("Hello.", state=state)
    assert decision.action == "block"
    assert "repeated" in decision.reason.lower()


def test_two_hostility_events_do_not_immediately_block() -> None:
    """Two warn events alone don't hard-block (but may warn)."""
    state = _state_with_events([_warn_event(), _warn_event()])
    decision = guard.evaluate("This is confusing.", state=state)
    # Should be warn or allow — not block — on a neutral message with 2 events.
    assert decision.action in {"allow", "warn"}


def test_build_safety_event_structure() -> None:
    decision = guard.evaluate("You are fucking stupid.")
    event = InputSafetyGuard.build_safety_event("You are fucking stupid.", decision)
    assert "action" in event
    assert event["action"] == decision.action
    assert "recorded_at" in event
    assert "severity" in event


def test_severity_zero_for_clean_message() -> None:
    decision = guard.evaluate("I need help with editing my manuscript.")
    assert decision.severity == 0
    assert decision.action == "allow"


def test_severity_high_for_threat() -> None:
    decision = guard.evaluate("I will hurt your team.")
    assert decision.severity >= 4


def test_audit_populated() -> None:
    decision = guard.evaluate("This is fucking confusing.")
    assert decision.audit
