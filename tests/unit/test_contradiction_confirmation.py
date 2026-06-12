"""Tests for contradiction-confirmation flow in ResponsePlanner."""
from __future__ import annotations

from bookcraft.components.context.schemas import ContextPack
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.response.planner import ResponsePlan, ResponsePlanner
from bookcraft.components.trg.schemas import ContradictionEvent, TRGContext
from bookcraft.domain.enums import QueryIntentType, SalesStage
from bookcraft.domain.state import ThreadState


def _make_intent() -> IntentVote:
    return IntentVote(
        query_primary=QueryIntentType.SERVICE_QUESTION,
        service_primary=None,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=False,
        confidence=0.9,
        rationale="test",
        evidence=[],
    )


def _make_context_pack(**kwargs) -> ContextPack:
    defaults = dict(
        missing_facts=[],
        forbidden_reasks=[],
        allowed_next_questions=[],
        known_facts=[],
    )
    defaults.update(kwargs)
    return ContextPack(**defaults)


def _make_planner(enabled: bool = True) -> ResponsePlanner:
    """ResponsePlanner is not a dataclass — set class attribute after construction."""
    planner = ResponsePlanner()
    planner.contradiction_confirmation_enabled = enabled
    return planner


class TestResponsePlanContradictionFields:
    def test_contradiction_pending_default_false(self):
        plan = ResponsePlan()
        assert plan.contradiction_pending is False

    def test_contradiction_hint_default_none(self):
        plan = ResponsePlan()
        assert plan.contradiction_hint is None

    def test_can_set_contradiction_pending(self):
        plan = ResponsePlan(
            contradiction_pending=True,
            contradiction_hint="word count conflict",
        )
        assert plan.contradiction_pending is True
        assert plan.contradiction_hint == "word count conflict"


class TestResponsePlannerContradiction:
    def test_planner_has_contradiction_confirmation_enabled(self):
        planner = ResponsePlanner()
        assert hasattr(planner, "contradiction_confirmation_enabled")
        assert planner.contradiction_confirmation_enabled is False

    def test_no_trg_context_no_contradiction(self):
        planner = _make_planner(enabled=True)
        intent = _make_intent()
        plan = planner.plan(
            intent=intent,
            state=ThreadState(),
            context_pack=_make_context_pack(),
            trg_context=None,
        )
        assert plan.contradiction_pending is False

    def test_empty_trg_context_no_contradiction(self):
        planner = _make_planner(enabled=True)
        intent = _make_intent()
        plan = planner.plan(
            intent=intent,
            state=ThreadState(),
            context_pack=_make_context_pack(),
            trg_context=TRGContext(),
        )
        assert plan.contradiction_pending is False

    def test_pricing_contradiction_sets_pending_when_enabled(self):
        planner = _make_planner(enabled=True)
        intent = _make_intent()

        contradiction = ContradictionEvent(
            fact_path="project.word_count",
            old_value="60000",
            new_value="80000",
            resolution_status="unresolved",
        )
        trg_context = TRGContext(contradictions=[contradiction])

        plan = planner.plan(
            intent=intent,
            state=ThreadState(),
            context_pack=_make_context_pack(),
            trg_context=trg_context,
        )
        assert plan.contradiction_pending is True
        assert plan.contradiction_hint is not None

    def test_pricing_contradiction_hint_mentions_field(self):
        planner = _make_planner(enabled=True)
        intent = _make_intent()

        contradiction = ContradictionEvent(
            fact_path="project.word_count",
            old_value="60000",
            new_value="80000",
            resolution_status="unresolved",
        )
        trg_context = TRGContext(contradictions=[contradiction])

        plan = planner.plan(
            intent=intent,
            state=ThreadState(),
            context_pack=_make_context_pack(),
            trg_context=trg_context,
        )
        # hint should reference the field name
        assert "word_count" in plan.contradiction_hint

    def test_page_count_contradiction_sets_pending(self):
        planner = _make_planner(enabled=True)
        intent = _make_intent()

        contradiction = ContradictionEvent(
            fact_path="project.page_count",
            old_value="200",
            new_value="300",
            resolution_status="unresolved",
        )
        trg_context = TRGContext(contradictions=[contradiction])

        plan = planner.plan(
            intent=intent,
            state=ThreadState(),
            context_pack=_make_context_pack(),
            trg_context=trg_context,
        )
        assert plan.contradiction_pending is True

    def test_non_pricing_contradiction_no_pending(self):
        """Genre is not a pricing-relevant field — should not set contradiction_pending."""
        planner = _make_planner(enabled=True)
        intent = _make_intent()

        contradiction = ContradictionEvent(
            fact_path="project.genre",
            old_value="fantasy",
            new_value="sci-fi",
            resolution_status="unresolved",
        )
        trg_context = TRGContext(contradictions=[contradiction])

        plan = planner.plan(
            intent=intent,
            state=ThreadState(),
            context_pack=_make_context_pack(),
            trg_context=trg_context,
        )
        assert plan.contradiction_pending is False

    def test_flag_disabled_no_contradiction_set(self):
        planner = _make_planner(enabled=False)
        intent = _make_intent()

        contradiction = ContradictionEvent(
            fact_path="project.word_count",
            old_value="60000",
            new_value="80000",
            resolution_status="unresolved",
        )
        trg_context = TRGContext(contradictions=[contradiction])

        plan = planner.plan(
            intent=intent,
            state=ThreadState(),
            context_pack=_make_context_pack(),
            trg_context=trg_context,
        )
        assert plan.contradiction_pending is False

    def test_resolved_contradiction_not_pending(self):
        """A resolved contradiction should not trigger pending."""
        planner = _make_planner(enabled=True)
        intent = _make_intent()

        contradiction = ContradictionEvent(
            fact_path="project.word_count",
            old_value="60000",
            new_value="80000",
            resolution_status="resolved",  # already resolved
        )
        trg_context = TRGContext(contradictions=[contradiction])

        plan = planner.plan(
            intent=intent,
            state=ThreadState(),
            context_pack=_make_context_pack(),
            trg_context=trg_context,
        )
        assert plan.contradiction_pending is False
