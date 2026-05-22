"""Gap 1 tests: long-tail intents map to real goals, not continue_discovery.

Verifies that all 19 query types produce a specific, meaningful primary_goal
rather than the generic 'continue_discovery' reflex.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bookcraft.components.response.planner import ResponsePlanner
from bookcraft.components.response.style_policy import PRIMARY_GOAL_GUIDANCE, ResponseStylePolicy
from bookcraft.domain.enums import QueryIntentType
from bookcraft.domain.state import ThreadState


def _intent(query: QueryIntentType) -> MagicMock:
    m = MagicMock()
    m.query_primary = query
    m.service_primary = None
    m.needs_clarification = False
    return m


def _context_pack(is_greeting: bool = False) -> MagicMock:
    cp = MagicMock()
    cp.lead_created = False
    cp.is_greeting_turn = is_greeting
    cp.contact_ready = False
    cp.assessment_type = None
    cp.active_service = None
    cp.active_genre = None
    cp.manuscript_status = None
    cp.known_facts = []
    cp.missing_facts = []
    cp.forbidden_reasks = []
    cp.allowed_next_questions = []
    cp.disallowed_next_questions = []
    cp.delegated_slots = []
    cp.preferred_call_time = None
    cp.response_hint = None
    cp.project_event = None
    cp.attachments_received = []
    cp.assessment_type = None
    cp.specialist_role = None
    return cp


_planner = ResponsePlanner()
_style = ResponseStylePolicy.default()


# ---------------------------------------------------------------------------
# Parametrized table: every long-tail intent must NOT map to continue_discovery
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query_type,expected_goal_fragment",
    [
        (QueryIntentType.SERVICE_QUESTION, "answer_current_question"),
        (QueryIntentType.REVISION_QUESTION, "revision_response"),
        (QueryIntentType.PAYMENT_QUESTION, "payment_guidance"),
        (QueryIntentType.MANUSCRIPT_STATUS_UPDATE, "celebrate_and_advance"),
        (QueryIntentType.COMPLAINT_OR_OBJECTION, "complaint_recovery"),
        (QueryIntentType.UNCLEAR, "gentle_clarify"),
        (QueryIntentType.SPAM_OR_ABUSE, "minimal_acknowledge"),
        (QueryIntentType.OFF_TOPIC, "friendly_redirect"),
        (QueryIntentType.PUBLISHING_PLATFORM_QUESTION, "answer_current_question"),
    ],
)
def test_long_tail_intent_maps_to_specific_goal(
    query_type: QueryIntentType,
    expected_goal_fragment: str,
) -> None:
    """Long-tail intent must map to a named goal, not continue_discovery."""
    plan = _planner.plan(
        intent=_intent(query_type),
        state=ThreadState(),
        context_pack=_context_pack(),
    )
    assert plan.primary_goal == expected_goal_fragment, (
        f"{query_type.value} → expected '{expected_goal_fragment}', got '{plan.primary_goal}'"
    )
    assert plan.primary_goal != "continue_discovery", (
        f"{query_type.value} must not collapse to generic continue_discovery"
    )


# ---------------------------------------------------------------------------
# Scoping-suppression: certain goals must not produce scoping next_questions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query_type",
    [
        QueryIntentType.REVISION_QUESTION,
        QueryIntentType.PAYMENT_QUESTION,
        QueryIntentType.MANUSCRIPT_STATUS_UPDATE,
        QueryIntentType.COMPLAINT_OR_OBJECTION,
        QueryIntentType.SPAM_OR_ABUSE,
        QueryIntentType.OFF_TOPIC,
    ],
)
def test_long_tail_goals_do_not_trigger_scoping_questions(query_type: QueryIntentType) -> None:
    """These goals must not produce word_count/genre/manuscript_stage next_questions."""
    plan = _planner.plan(
        intent=_intent(query_type),
        state=ThreadState(),
        context_pack=_context_pack(),
    )
    assert plan.next_question not in {
        "word_or_page_count",
        "genre",
        "manuscript_stage",
        "deadline",
    }, (
        f"{query_type.value} goal='{plan.primary_goal}' must not produce scoping next_question, "
        f"got: {plan.next_question}"
    )


# ---------------------------------------------------------------------------
# PRIMARY_GOAL_GUIDANCE entries must exist for every new goal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "goal",
    [
        "revision_response",
        "payment_guidance",
        "celebrate_and_advance",
        "complaint_recovery",
        "gentle_clarify",
        "minimal_acknowledge",
        "friendly_redirect",
        "greeting_welcome",
        "answer_current_question",
        "lead_contact_capture",
    ],
)
def test_primary_goal_guidance_exists(goal: str) -> None:
    """Every named primary_goal must have guidance in PRIMARY_GOAL_GUIDANCE."""
    assert goal in PRIMARY_GOAL_GUIDANCE, (
        f"PRIMARY_GOAL_GUIDANCE is missing entry for '{goal}' — "
        "the LLM will not receive goal-specific instructions on this turn."
    )


# ---------------------------------------------------------------------------
# style_policy prompt section contains the guidance for new goals
# ---------------------------------------------------------------------------


def test_revision_goal_prompt_contains_revision_guidance() -> None:
    from bookcraft.components.response.generator import _response_plan_prompt_section
    from bookcraft.components.response.planner import ResponsePlan

    plan = ResponsePlan(primary_goal="revision_response")
    section = _response_plan_prompt_section(plan)
    assert "revision_response" in section
    assert "revision" in section.lower() or "version" in section.lower()


def test_complaint_recovery_goal_no_scoping_instruction() -> None:
    from bookcraft.components.response.generator import _response_plan_prompt_section
    from bookcraft.components.response.planner import ResponsePlan

    plan = ResponsePlan(primary_goal="complaint_recovery")
    section = _response_plan_prompt_section(plan)
    assert "complaint_recovery" in section
    assert "word count" not in section.lower()
    assert "genre" not in section.lower()


def test_celebrate_and_advance_no_scoping_instruction() -> None:
    from bookcraft.components.response.generator import _response_plan_prompt_section
    from bookcraft.components.response.planner import ResponsePlan

    plan = ResponsePlan(primary_goal="celebrate_and_advance")
    section = _response_plan_prompt_section(plan)
    assert "celebrate_and_advance" in section
    lower = section.lower()
    assert "celebrate" in lower or "milestone" in lower or "advance" in lower
