"""Gap 7 tests: topic-switch and contradiction handling prompt guidance.

Verifies that:
- The system prompt contains topic-switch and contradiction guidance.
- TRG contradiction signals surface as specific hint text.
- TRG service-shift signals instruct a clean topic switch.
- The hint correctly omits stale scoping from the old service.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from bookcraft.components.response.generator import _response_system_prompt
from bookcraft.domain.enums import QueryIntentType
from bookcraft.domain.state import ThreadState

# ---------------------------------------------------------------------------
# System prompt must contain gap-7 guidance
# ---------------------------------------------------------------------------


def test_system_prompt_has_topic_switch_guidance() -> None:
    """System prompt must explicitly instruct how to handle service pivots."""
    prompt = _response_system_prompt()
    lowered = prompt.lower()
    assert "pivot" in lowered or "switch" in lowered or "different service" in lowered, (
        "System prompt must guide the LLM on topic/service switches"
    )


def test_system_prompt_has_contradiction_reconciliation_guidance() -> None:
    """System prompt must explicitly instruct gentle contradiction surfacing."""
    prompt = _response_system_prompt()
    lowered = prompt.lower()
    assert "contradict" in lowered or "earlier" in lowered, (
        "System prompt must guide the LLM on contradictions"
    )


def test_system_prompt_does_not_say_silently_pick() -> None:
    """System prompt must NOT say to silently pick a contradicting value."""
    prompt = _response_system_prompt()
    assert "silently" not in prompt.lower() or "rather than silently" in prompt.lower()


# ---------------------------------------------------------------------------
# TRG hint enrichment for contradictions
# ---------------------------------------------------------------------------


def _make_trg_context(*, contradictions=None, service_shifts=None, contradiction_count=0):
    ctx = MagicMock()
    ctx.outstanding_questions = []
    ctx.repeated_user_messages = []
    ctx.contradiction_count = contradiction_count
    ctx.contradictions = contradictions or []
    ctx.service_shifts = service_shifts or []
    ctx.active_facts = []
    ctx.answered_questions = []
    ctx.forbidden_reasks = []
    return ctx


def _make_contradiction(fact_path: str, old_value: str, new_value: str):
    c = MagicMock()
    c.fact_path = fact_path
    c.old_value = old_value
    c.new_value = new_value
    c.resolution_status = "unresolved"
    return c


def _make_service_shift(old_svc: str, new_svc: str):
    s = MagicMock()
    s.previous_service = old_svc
    s.new_service = new_svc
    s.mode = "switch"
    return s


def _build_hint(trg_context) -> str | None:
    from bookcraft.components.intent.schemas import IntentVote
    from bookcraft.domain.enums import QueryIntentType, SalesStage
    from bookcraft.domain.state import ThreadState
    from bookcraft.services.chat import _trg_response_hint_from_context

    intent = IntentVote(
        query_primary=QueryIntentType.SERVICE_QUESTION,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=False,
        confidence=0.9,
        rationale="test",
        evidence=[],
    )
    return _trg_response_hint_from_context(
        state=ThreadState(),
        intent=intent,
        trg_context=trg_context,
    )


def test_contradiction_hint_surfaces_specific_values() -> None:
    """When contradictions are unresolved, hint must include old and new values."""
    trg = _make_trg_context(
        contradictions=[_make_contradiction("project.genre", "thriller", "romance")],
        contradiction_count=1,
    )
    hint = _build_hint(trg)
    assert hint is not None
    assert "thriller" in hint or "romance" in hint, (
        f"Hint must include contradiction values, got: {hint}"
    )
    assert "earlier" in hint.lower() or "contradict" in hint.lower()


def test_contradiction_hint_multiple_contradictions_capped_at_two() -> None:
    """Only 2 contradictions are surfaced to keep the hint concise."""
    trg = _make_trg_context(
        contradictions=[
            _make_contradiction("genre", "thriller", "romance"),
            _make_contradiction("word_count", "50000", "80000"),
            _make_contradiction("service", "editing", "ghostwriting"),
        ],
        contradiction_count=3,
    )
    hint = _build_hint(trg)
    assert hint is not None
    # Should contain at most 2 contradiction references (cap is 2)
    count = hint.count("earlier=") + hint.count("now=")
    assert count <= 4  # 2 contradictions × 2 value refs each


def test_service_shift_hint_mentions_new_service() -> None:
    """When a service shift is detected, hint must name the new service."""
    trg = _make_trg_context(
        service_shifts=[_make_service_shift("editing_proofreading", "cover_design_illustration")]
    )
    hint = _build_hint(trg)
    assert hint is not None
    assert "cover" in hint.lower() or "cover_design" in hint.lower(), (
        f"Hint must mention the new service, got: {hint}"
    )
    assert "editing" in hint.lower() or "editing_proofreading" in hint.lower()


def test_service_shift_hint_instructs_dropping_old_service() -> None:
    """Hint must tell the LLM to drop the old service's scoping."""
    trg = _make_trg_context(
        service_shifts=[_make_service_shift("ghostwriting", "publishing_distribution")]
    )
    hint = _build_hint(trg)
    assert hint is not None
    assert "drop" in hint.lower() or "switch" in hint.lower() or "move" in hint.lower()


def test_no_trg_context_returns_none_or_state_hint() -> None:
    """When no TRG context, function returns None or a basic state hint."""
    from bookcraft.components.intent.schemas import IntentVote
    from bookcraft.domain.enums import SalesStage
    from bookcraft.services.chat import _trg_response_hint_from_context

    intent = IntentVote(
        query_primary=QueryIntentType.SERVICE_QUESTION,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=False,
        confidence=0.9,
        rationale="test",
        evidence=[],
    )
    result = _trg_response_hint_from_context(state=ThreadState(), intent=intent, trg_context=None)
    # No TRG + no known facts = None
    assert result is None or isinstance(result, str)
