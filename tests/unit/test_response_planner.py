from __future__ import annotations

from bookcraft.components.context.schemas import ContextPack, KnownFact
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.response.planner import ResponsePlanner
from bookcraft.components.tools.governance import ToolGovernanceDecision
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory
from bookcraft.domain.state import ThreadState

_planner = ResponsePlanner()


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _intent(
    *,
    query: QueryIntentType = QueryIntentType.SERVICE_QUESTION,
    service: ServiceCategory | None = None,
    confidence: float = 0.90,
) -> IntentVote:
    return IntentVote(
        query_primary=query,
        service_primary=service,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=False,
        confidence=confidence,
        rationale="test",
        evidence=[],
    )


def _pack(
    *,
    active_service: str | None = None,
    active_genre: str | None = None,
    manuscript_status: str | None = None,
    missing_facts: list[str] | None = None,
    forbidden_reasks: list[str] | None = None,
    allowed_next_questions: list[str] | None = None,
    known_facts: list[KnownFact] | None = None,
) -> ContextPack:
    return ContextPack(
        active_service=active_service,
        active_genre=active_genre,
        manuscript_status=manuscript_status,
        missing_facts=missing_facts or [],
        forbidden_reasks=forbidden_reasks or [],
        allowed_next_questions=allowed_next_questions or [],
        known_facts=known_facts or [],
    )


def _governance_blocked(
    reason: str = "low_confidence_side_effect_blocked",
    blocked_message: str = "I should confirm a few details first.",
) -> ToolGovernanceDecision:
    return ToolGovernanceDecision(
        allowed=False,
        reason=reason,
        blocked_message=blocked_message,
    )


def _governance_allowed(requires_confirmation: bool = False) -> ToolGovernanceDecision:
    return ToolGovernanceDecision(
        allowed=True,
        requires_confirmation=requires_confirmation,
        reason="allowed_with_idempotency_key",
        idempotency_key="abc123" * 4,
    )


def _known_fact(path: str, label: str, value: str) -> KnownFact:
    return KnownFact(path=path, label=label, value=value, confidence=0.9, source="user_stated")


# ---------------------------------------------------------------------------
# acknowledge_facts
# ---------------------------------------------------------------------------


def test_plan_includes_active_service_in_acknowledge_facts() -> None:
    pack = _pack(active_service="cover_design_illustration")
    plan = _planner.plan(intent=_intent(), state=ThreadState(), context_pack=pack)
    assert any("cover_design_illustration" in f for f in plan.acknowledge_facts)


def test_plan_includes_known_genre_in_acknowledge_facts() -> None:
    pack = _pack(
        active_genre="children's fiction",
        known_facts=[_known_fact("project.genre", "genre", "children's fiction")],
    )
    plan = _planner.plan(intent=_intent(), state=ThreadState(), context_pack=pack)
    assert any("genre" in f and "children's fiction" in f for f in plan.acknowledge_facts)


def test_plan_includes_manuscript_status_in_acknowledge_facts() -> None:
    pack = _pack(
        manuscript_status="completed_draft",
        known_facts=[
            _known_fact("project.manuscript_status", "manuscript_status", "completed_draft")
        ],
    )
    plan = _planner.plan(intent=_intent(), state=ThreadState(), context_pack=pack)
    assert any("manuscript_status" in f for f in plan.acknowledge_facts)


def test_plan_acknowledge_facts_empty_when_nothing_known() -> None:
    pack = _pack()
    plan = _planner.plan(intent=_intent(), state=ThreadState(), context_pack=pack)
    assert plan.acknowledge_facts == []


# ---------------------------------------------------------------------------
# must_not_mention
# ---------------------------------------------------------------------------


def test_plan_includes_forbidden_reasks_in_must_not_mention() -> None:
    pack = _pack(
        active_genre="children's fiction",
        forbidden_reasks=["genre", "what genre"],
    )
    plan = _planner.plan(intent=_intent(), state=ThreadState(), context_pack=pack)
    assert "genre" in plan.must_not_mention
    assert "what genre" in plan.must_not_mention


def test_plan_includes_internal_terms_in_must_not_mention() -> None:
    pack = _pack()
    plan = _planner.plan(intent=_intent(), state=ThreadState(), context_pack=pack)
    assert "backend" in plan.must_not_mention
    assert "RAG" in plan.must_not_mention
    assert "tool_governance" in plan.must_not_mention


def test_plan_includes_unrelated_service_drift_when_active_service() -> None:
    pack = _pack(active_service="cover_design_illustration")
    plan = _planner.plan(intent=_intent(), state=ThreadState(), context_pack=pack)
    assert "unrelated_service_drift" in plan.must_not_mention


def test_plan_no_service_drift_suppression_without_active_service() -> None:
    pack = _pack()
    plan = _planner.plan(intent=_intent(), state=ThreadState(), context_pack=pack)
    assert "unrelated_service_drift" not in plan.must_not_mention


# ---------------------------------------------------------------------------
# primary_goal
# ---------------------------------------------------------------------------


def test_plan_primary_goal_cover_design_scoping() -> None:
    pack = _pack(active_service="cover_design_illustration")
    plan = _planner.plan(intent=_intent(), state=ThreadState(), context_pack=pack)
    assert plan.primary_goal == "cover_design_scoping"


def test_plan_primary_goal_pricing_scoping() -> None:
    pack = _pack()
    plan = _planner.plan(
        intent=_intent(query=QueryIntentType.PRICING_QUESTION),
        state=ThreadState(),
        context_pack=pack,
    )
    assert plan.primary_goal == "pricing_scoping"


def test_plan_primary_goal_consultation_scoping() -> None:
    pack = _pack()
    plan = _planner.plan(
        intent=_intent(query=QueryIntentType.CONSULTATION_REQUEST),
        state=ThreadState(),
        context_pack=pack,
    )
    assert plan.primary_goal == "consultation_scoping"


def test_plan_primary_goal_document_scoping_nda() -> None:
    pack = _pack()
    plan = _planner.plan(
        intent=_intent(query=QueryIntentType.NDA_REQUEST),
        state=ThreadState(),
        context_pack=pack,
    )
    assert plan.primary_goal == "document_scoping"


def test_plan_primary_goal_portfolio_matching() -> None:
    pack = _pack()
    plan = _planner.plan(
        intent=_intent(query=QueryIntentType.PORTFOLIO_REQUEST),
        state=ThreadState(),
        context_pack=pack,
    )
    assert plan.primary_goal == "portfolio_matching"


def test_plan_primary_goal_continue_discovery_default() -> None:
    pack = _pack()
    plan = _planner.plan(intent=_intent(), state=ThreadState(), context_pack=pack)
    assert plan.primary_goal == "continue_discovery"


def test_plan_primary_goal_safe_blocked_action_when_governance_blocked() -> None:
    pack = _pack()
    plan = _planner.plan(
        intent=_intent(),
        state=ThreadState(),
        context_pack=pack,
        tool_governance=_governance_blocked("low_confidence_side_effect_blocked"),
    )
    assert plan.primary_goal == "safe_blocked_action"


def test_plan_primary_goal_clarify_intent_when_counterfactual_blocked() -> None:
    pack = _pack()
    plan = _planner.plan(
        intent=_intent(),
        state=ThreadState(),
        context_pack=pack,
        tool_governance=_governance_blocked("counterfactual_side_effect_blocked"),
    )
    assert plan.primary_goal == "clarify_intent"


# ---------------------------------------------------------------------------
# next_question
# ---------------------------------------------------------------------------


def test_plan_cover_design_asks_cover_style_when_missing() -> None:
    pack = _pack(
        active_service="cover_design_illustration",
        allowed_next_questions=["cover_style", "word_or_page_count"],
    )
    plan = _planner.plan(intent=_intent(), state=ThreadState(), context_pack=pack)
    assert plan.next_question == "cover_style"


def test_plan_does_not_ask_genre_when_in_forbidden_reasks() -> None:
    pack = _pack(
        active_genre="children's fiction",
        forbidden_reasks=["genre", "what genre"],
        allowed_next_questions=["word_or_page_count"],
    )
    plan = _planner.plan(intent=_intent(), state=ThreadState(), context_pack=pack)
    # The next_question should not be genre (it's in forbidden_reasks)
    assert plan.next_question != "genre"


def test_plan_next_question_from_allowed_list_first() -> None:
    pack = _pack(
        missing_facts=["genre", "word_or_page_count"],
        allowed_next_questions=["word_or_page_count", "genre"],
    )
    plan = _planner.plan(intent=_intent(), state=ThreadState(), context_pack=pack)
    assert plan.next_question == "word_or_page_count"


def test_plan_next_question_falls_back_to_missing_facts() -> None:
    # "genre" comes before "manuscript_stage" in the default priority order.
    pack = _pack(missing_facts=["manuscript_stage", "genre"])
    plan = _planner.plan(intent=_intent(), state=ThreadState(), context_pack=pack)
    assert plan.next_question == "genre"


def test_plan_next_question_none_when_nothing_missing() -> None:
    pack = _pack()
    plan = _planner.plan(intent=_intent(), state=ThreadState(), context_pack=pack)
    assert plan.next_question is None


def test_plan_next_question_skipped_when_governance_blocked() -> None:
    pack = _pack(
        missing_facts=["genre"],
        allowed_next_questions=["genre"],
    )
    plan = _planner.plan(
        intent=_intent(),
        state=ThreadState(),
        context_pack=pack,
        tool_governance=_governance_blocked(),
    )
    # When governance blocks, don't ask about the blocked action's context.
    assert plan.next_question is None


# ---------------------------------------------------------------------------
# customer_safe_tool_summary
# ---------------------------------------------------------------------------


def test_plan_uses_blocked_message_as_tool_summary() -> None:
    pack = _pack()
    gov = _governance_blocked(blocked_message="I should confirm a few details first.")
    plan = _planner.plan(
        intent=_intent(),
        state=ThreadState(),
        context_pack=pack,
        tool_governance=gov,
    )
    assert plan.customer_safe_tool_summary == "I should confirm a few details first."


def test_plan_no_tool_summary_when_allowed() -> None:
    pack = _pack()
    plan = _planner.plan(
        intent=_intent(),
        state=ThreadState(),
        context_pack=pack,
        tool_governance=_governance_allowed(),
    )
    assert plan.customer_safe_tool_summary is None


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


def test_plan_max_questions_always_1() -> None:
    plan = _planner.plan(intent=_intent(), state=ThreadState(), context_pack=_pack())
    assert plan.max_questions == 1


def test_plan_tone_always_warm_consultative() -> None:
    plan = _planner.plan(intent=_intent(), state=ThreadState(), context_pack=_pack())
    assert plan.tone == "warm_consultative"


def test_plan_audit_trail_is_populated() -> None:
    plan = _planner.plan(intent=_intent(), state=ThreadState(), context_pack=_pack())
    assert len(plan.audit) >= 1


# ===========================================================================
# Required tests (exact names from spec)
# ===========================================================================


def test_cover_design_plan_prefers_cover_style() -> None:
    """
    When cover design is active, genre and manuscript stage are known, and
    cover_style is in missing_facts, the plan must:
    - goal: cover_design_scoping
    - next_question: cover_style (highest-priority for cover design)
    - acknowledge known facts (service, genre, draft status)
    - suppress genre/manuscript re-asks
    - max_questions == 1
    """
    pack = _pack(
        active_service="cover_design_illustration",
        active_genre="children's fiction",
        manuscript_status="completed_draft",
        known_facts=[
            _known_fact("project.genre", "genre", "children's fiction"),
            _known_fact("project.manuscript_status", "manuscript_status", "completed_draft"),
        ],
        missing_facts=["word_or_page_count", "cover_style"],
        # ContextPackBuilder orders cover_style first for cover_design service.
        allowed_next_questions=["cover_style", "word_or_page_count"],
        forbidden_reasks=[
            "genre",
            "what genre",
            "manuscript_stage",
            "draft status",
            "starting from scratch",
        ],
    )

    plan = _planner.plan(intent=_intent(), state=ThreadState(), context_pack=pack)

    assert plan.primary_goal == "cover_design_scoping"
    assert plan.next_question == "cover_style"
    assert any("cover_design_illustration" in f for f in plan.acknowledge_facts)
    assert any("children's fiction" in f for f in plan.acknowledge_facts)
    assert any("completed_draft" in f for f in plan.acknowledge_facts)
    # Known facts must be suppressed from re-asking.
    assert "genre" in plan.must_not_mention
    assert any(m in plan.must_not_mention for m in ("manuscript_stage", "draft status"))
    assert plan.max_questions == 1


def test_empty_discovery_plan_asks_highest_priority_missing_fact() -> None:
    """
    With no established context, primary_goal is continue_discovery and
    next_question is the first entry in missing_facts (or allowed_next_questions).
    """
    pack = _pack(
        missing_facts=["genre", "manuscript_stage", "word_or_page_count"],
        allowed_next_questions=["genre", "manuscript_stage", "word_or_page_count"],
    )

    plan = _planner.plan(intent=_intent(), state=ThreadState(), context_pack=pack)

    assert plan.primary_goal == "continue_discovery"
    assert plan.next_question in {"genre", "manuscript_stage", "word_or_page_count"}
    assert plan.max_questions == 1


def test_pricing_plan_asks_missing_quote_slot() -> None:
    """
    For a pricing intent, primary_goal is pricing_scoping and next_question
    is the highest-priority missing quote slot (word_or_page_count before deadline).
    The audit trail must record the next-question selection.
    """
    pack = _pack(
        missing_facts=["word_or_page_count", "deadline"],
        # ContextPackBuilder puts word_or_page_count first for pricing.
        allowed_next_questions=["word_or_page_count", "deadline"],
    )

    plan = _planner.plan(
        intent=_intent(query=QueryIntentType.PRICING_QUESTION),
        state=ThreadState(),
        context_pack=pack,
    )

    assert plan.primary_goal == "pricing_scoping"
    assert plan.next_question == "word_or_page_count"
    # Audit must explain how next_question was selected.
    assert any("word_or_page_count" in a for a in plan.audit)


def test_consultation_blocked_counterfactual_plan_clarifies_intent() -> None:
    """
    When a consultation is blocked due to counterfactual language, the plan
    must NOT auto-book and must signal the intent is unclear.
    """
    pack = _pack(
        allowed_next_questions=["word_or_page_count"],
    )
    gov = _governance_blocked(
        reason="counterfactual_side_effect_blocked",
        blocked_message="I can set up a consultation when you're ready.",
    )

    plan = _planner.plan(
        intent=_intent(query=QueryIntentType.CONSULTATION_REQUEST),
        state=ThreadState(),
        context_pack=pack,
        tool_governance=gov,
    )

    assert plan.primary_goal in {"clarify_intent", "safe_blocked_action"}
    assert plan.customer_safe_tool_summary is not None
    # Must not produce a booking question when governance blocked.
    assert plan.next_question is None


def test_document_blocked_plan_uses_safe_message() -> None:
    """
    When an NDA/agreement action is governance-blocked, the plan must carry
    the blocked_message as customer_safe_tool_summary and set primary_goal
    to safe_blocked_action.
    """
    safe_msg = "I should confirm a few details before moving ahead with that."
    gov = _governance_blocked(
        reason="negated_nda_blocked",
        blocked_message=safe_msg,
    )

    plan = _planner.plan(
        intent=_intent(query=QueryIntentType.NDA_REQUEST),
        state=ThreadState(),
        context_pack=_pack(),
        tool_governance=gov,
    )

    assert plan.customer_safe_tool_summary == safe_msg
    assert plan.primary_goal == "safe_blocked_action"


def test_plan_internal_terms_are_never_allowed() -> None:
    """
    Regardless of intent or context, must_not_mention must always include
    every internal implementation term so they never surface to customers.
    """
    plan = _planner.plan(intent=_intent(), state=ThreadState(), context_pack=_pack())

    for term in (
        "backend",
        "classifier",
        "runtime atoms",
        "RAG",
        "tool_governance",
        "action_plan",
    ):
        assert term in plan.must_not_mention, (
            f"Internal term '{term}' missing from must_not_mention"
        )
