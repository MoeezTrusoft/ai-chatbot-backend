"""Step 5 tests: consultant-tone prompt, source-grounded, goal-aware first pass."""

from __future__ import annotations

from bookcraft.components.response.generator import (
    _response_plan_prompt_section,
    _response_system_prompt,
)
from bookcraft.components.response.planner import ResponsePlan
from bookcraft.components.response.style_policy import ResponseStylePolicy


def test_system_prompt_does_not_say_always_ask_question() -> None:
    """System prompt must NOT instruct 'always ask a next-step question' rigidly."""
    prompt = _response_system_prompt()
    lowered = prompt.lower()
    # Old formula said "ask one clear next-step question" as a rigid instruction
    assert "ask one clear next-step question" not in lowered, (
        "System prompt must not rigidly mandate a question on every turn"
    )


def test_system_prompt_instructs_welcome_first() -> None:
    """Prompt must include welcome/engage instruction for first messages."""
    prompt = _response_system_prompt()
    lowered = prompt.lower()
    assert "first message" in lowered or "welcome" in lowered or "engage" in lowered


def test_system_prompt_answer_before_contact_ask() -> None:
    """Prompt must say to answer questions before asking for contact."""
    prompt = _response_system_prompt()
    lowered = prompt.lower()
    assert "answer" in lowered or "question" in lowered


def test_system_prompt_source_grounded_instruction() -> None:
    """Prompt must instruct to use provided context as source of truth."""
    prompt = _response_system_prompt()
    lowered = prompt.lower()
    assert "source of truth" in lowered or "ground" in lowered


def test_greeting_goal_prompt_includes_welcome_guidance() -> None:
    """Response plan section for greeting_welcome must include welcome + no contact ask."""
    plan = ResponsePlan(primary_goal="greeting_welcome")
    section = _response_plan_prompt_section(plan)
    assert "greeting_welcome" in section
    lower = section.lower()
    assert "contact" in lower or "welcome" in lower or "engage" in lower


def test_answer_question_goal_prompt_does_not_mandate_contact() -> None:
    """Response plan section for answer_current_question must not demand contact."""
    plan = ResponsePlan(primary_goal="answer_current_question")
    section = _response_plan_prompt_section(plan)
    assert "answer_current_question" in section
    # Should guide to answer, NOT demand contact
    assert "name and email" not in section.lower()


def test_contact_capture_goal_asks_one_channel_not_both() -> None:
    """lead_contact_capture must ask for ONE channel (email or phone), not both."""
    plan = ResponsePlan(primary_goal="lead_contact_capture")
    section = _response_plan_prompt_section(plan)
    # Should say "OR phone" not "AND phone"
    lower = section.lower()
    assert "email or phone" in lower or "one contact" in lower or "or phone" in lower


def test_style_instructions_no_rigid_formula() -> None:
    """style_instructions must not rigidly mandate 'ask one clear next-step question'."""
    policy = ResponseStylePolicy.default()
    instructions = policy.style_instructions()
    # Old formula was: "Formula: Acknowledge -> Interpret -> Move one step forward."
    # followed by "ask one clear next-step question."
    assert "ask one clear next-step question" not in instructions


def test_style_instructions_welcome_first() -> None:
    """style_instructions must say to welcome first before asking for contact."""
    policy = ResponseStylePolicy.default()
    instructions = policy.style_instructions()
    lowered = instructions.lower()
    assert "first message" in lowered or "welcome" in lowered or "engage" in lowered


def test_preferred_openers_no_based_on_what_you_shared() -> None:
    """'Based on what you shared' must be removed from PREFERRED_OPENERS."""
    from bookcraft.components.response.style_policy import PREFERRED_OPENERS

    assert not any("Based on what you shared" in opener for opener in PREFERRED_OPENERS), (
        "'Based on what you shared' conflicts with missing_specificity check — must be removed"
    )
