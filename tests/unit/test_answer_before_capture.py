"""Tests for AnswerBeforeCapturePolicy."""

from __future__ import annotations

import pytest

from bookcraft.components.sales.answer_before_capture import AnswerBeforeCapturePolicy
from bookcraft.components.sales.current_question_priority import (
    CurrentQuestionPriorityDetector,
    CurrentQuestionPriorityResult,
)


@pytest.fixture
def policy() -> AnswerBeforeCapturePolicy:
    return AnswerBeforeCapturePolicy()


@pytest.fixture
def detector() -> CurrentQuestionPriorityDetector:
    return CurrentQuestionPriorityDetector()


def _priority(text: str) -> CurrentQuestionPriorityResult:
    return CurrentQuestionPriorityDetector().detect(text)


def test_pricing_answers_before_contact(policy: AnswerBeforeCapturePolicy) -> None:
    p = _priority("How much does ghostwriting cost?")
    decision = policy.decide(priority=p, contact_ready=False)
    assert decision.should_answer_first is True
    assert decision.answer_focus == "pricing_explanation_scope_based"
    assert decision.boundary is not None
    assert "no_invented_price" in decision.boundary
    assert decision.suppress_contact_until_answered is True
    assert decision.consultation_bridge is True


def test_distribution_answers_before_contact(policy: AnswerBeforeCapturePolicy) -> None:
    p = _priority("Do you distribute on Amazon KDP?")
    decision = policy.decide(priority=p, contact_ready=False)
    assert decision.should_answer_first is True
    assert decision.answer_focus == "distribution_platform_support_explanation"
    assert decision.suppress_contact_until_answered is True


def test_contact_refusal_suppresses_contact_capture(policy: AnswerBeforeCapturePolicy) -> None:
    p = _priority("I don't want to share contact before knowing the price.")
    decision = policy.decide(priority=p, contact_ready=False)
    assert decision.should_answer_first is True
    assert decision.suppress_contact_until_answered is True
    # Contact refusal should NOT bridge to consultation (respect the refusal).
    assert decision.consultation_bridge is False


def test_christian_publishing_uses_safe_boundary(policy: AnswerBeforeCapturePolicy) -> None:
    p = _priority("Do you work with Christian publishers?")
    decision = policy.decide(priority=p, contact_ready=False)
    assert decision.should_answer_first is True
    assert decision.answer_focus == "faith_based_manuscript_support"
    assert "no_claimed_publisher_relationships" in (decision.boundary or "")


def test_no_priority_passes_through(policy: AnswerBeforeCapturePolicy) -> None:
    p = _priority("I need editing for my completed novel.")
    decision = policy.decide(priority=p)
    assert decision.should_answer_first is False
    assert decision.suppress_contact_until_answered is False


def test_fiverr_comparison_positive_positioning(policy: AnswerBeforeCapturePolicy) -> None:
    p = _priority("Why not just use Fiverr for this?")
    decision = policy.decide(priority=p, contact_ready=False)
    assert decision.should_answer_first is True
    assert decision.answer_focus == "professional_managed_service_positioning"
    assert decision.consultation_bridge is True


def test_contact_already_ready_does_not_suppress(policy: AnswerBeforeCapturePolicy) -> None:
    """When contact is already captured, suppression is not needed."""
    p = _priority("How much does editing cost?")
    decision = policy.decide(priority=p, contact_ready=True)
    assert decision.should_answer_first is True
    # suppress_contact_until_answered should be False when contact is already ready
    assert decision.suppress_contact_until_answered is False


def test_audit_populated(policy: AnswerBeforeCapturePolicy) -> None:
    p = _priority("How much does ghostwriting cost?")
    decision = policy.decide(priority=p)
    assert decision.audit
