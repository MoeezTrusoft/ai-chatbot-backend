from __future__ import annotations

from bookcraft.components.context.delegation import DelegatedDecision
from bookcraft.components.context.schemas import ContextPack
from bookcraft.components.intent.flexible_router import FlexibleIntentRouter
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.portfolio.fallback_policy import PortfolioFallbackDecision
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory
from bookcraft.domain.state import ThreadState

_router = FlexibleIntentRouter()


def _intent(
    query: QueryIntentType = QueryIntentType.SERVICE_QUESTION,
    service: ServiceCategory | None = None,
) -> IntentVote:
    return IntentVote(
        query_primary=query,
        service_primary=service,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=False,
        confidence=0.9,
        rationale="test",
    )


def _state() -> ThreadState:
    return ThreadState()


# ---------------------------------------------------------------------------
# 1. Unsure service → service_guidance
# ---------------------------------------------------------------------------


def test_detects_unsure_service_guidance() -> None:
    d = _router.route(
        text="I don't know what I need, can you guide me?",
        intent=_intent(),
        state=_state(),
    )
    assert d.detected is True
    assert d.mode == "service_guidance"
    assert d.recommended_primary_goal == "flexible_service_guidance"
    assert d.next_question == "manuscript_stage_or_project_status"


# ---------------------------------------------------------------------------
# 2. BookCraft discretion
# ---------------------------------------------------------------------------


def test_detects_bookcraft_discretion() -> None:
    d = _router.route(
        text="I trust your team — whatever you think is best.",
        intent=_intent(),
        state=_state(),
    )
    assert d.detected is True
    assert d.mode == "bookcraft_discretion"
    assert d.recommended_primary_goal == "consultation_handoff"
    assert d.next_question == "consultation_interest"


# ---------------------------------------------------------------------------
# 3. Consultation handoff
# ---------------------------------------------------------------------------


def test_detects_consultation_handoff() -> None:
    d = _router.route(
        text="Can I schedule a call with your team?",
        intent=_intent(),
        state=_state(),
    )
    assert d.detected is True
    assert d.mode == "consultation_handoff"
    assert d.recommended_primary_goal == "consultation_handoff"
    assert d.next_question == "consultation_interest"


# ---------------------------------------------------------------------------
# 4. Process explanation
# ---------------------------------------------------------------------------


def test_detects_process_explanation() -> None:
    d = _router.route(
        text="How does it work? What happens next?",
        intent=_intent(),
        state=_state(),
    )
    assert d.detected is True
    assert d.mode == "process_explanation"
    assert d.recommended_primary_goal == "process_explanation"


# ---------------------------------------------------------------------------
# 5. Delegated decision routes to bookcraft_discretion
# ---------------------------------------------------------------------------


def test_delegated_decision_routes_to_bookcraft_discretion() -> None:
    delegated = DelegatedDecision(
        detected=True,
        status="delegated",
        target_slot="cover_style",
        confidence=0.9,
        cue="you decide",
    )
    d = _router.route(
        text="Please handle it.",
        intent=_intent(),
        state=_state(),
        delegated_decision=delegated,
    )
    assert d.detected is True
    assert d.mode in ("bookcraft_discretion", "process_explanation", "consultation_handoff")


# ---------------------------------------------------------------------------
# 6. Explicit service request → not_flexible
# ---------------------------------------------------------------------------


def test_explicit_service_request_is_not_flexible() -> None:
    d = _router.route(
        text="I need editing and proofreading for my novel.",
        intent=_intent(service=ServiceCategory.EDITING_PROOFREADING),
        state=_state(),
    )
    assert d.detected is False
    assert d.mode == "not_flexible"


# ---------------------------------------------------------------------------
# 7. Portfolio fallback active → not overridden
# ---------------------------------------------------------------------------


def test_portfolio_fallback_not_overridden() -> None:
    pf = PortfolioFallbackDecision(
        strategy="fallback_general_samples",
        reason="user_insisted_on_samples",
    )
    d = _router.route(
        text="I don't know what I need, just show me samples.",
        intent=_intent(),
        state=_state(),
        portfolio_fallback_decision=pf,
    )
    assert d.detected is False
    assert d.mode == "not_flexible"
    assert d.recommended_primary_goal == "portfolio_matching"


# ---------------------------------------------------------------------------
# 8. Active service + guidance cue → process_explanation, not generic guidance
# ---------------------------------------------------------------------------


def test_active_cover_recommendation_routes_to_discretion_not_generic_guidance() -> None:
    pack = ContextPack(active_service="cover_design_illustration")
    d = _router.route(
        text="What do you recommend for cover design?",
        intent=_intent(service=ServiceCategory.COVER_DESIGN_ILLUSTRATION),
        state=_state(),
        context_pack=pack,
    )
    assert d.detected is True
    assert d.mode in ("process_explanation", "bookcraft_discretion")
    assert d.mode != "service_guidance"


# ---------------------------------------------------------------------------
# Extra: no false positive on plain service message
# ---------------------------------------------------------------------------


def test_no_false_positive_on_plain_book_inquiry() -> None:
    d = _router.route(
        text="I need help with my fantasy novel.",
        intent=_intent(),
        state=_state(),
    )
    assert d.detected is False


def test_consultation_takes_priority_over_guidance() -> None:
    d = _router.route(
        text="I don't know what I need — can I talk to someone?",
        intent=_intent(),
        state=_state(),
    )
    assert d.detected is True
    assert d.mode == "consultation_handoff"
