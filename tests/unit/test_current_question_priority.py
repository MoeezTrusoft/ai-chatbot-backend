"""Tests for CurrentQuestionPriorityDetector."""

from __future__ import annotations

import pytest

from bookcraft.components.sales.current_question_priority import CurrentQuestionPriorityDetector


@pytest.fixture
def detector() -> CurrentQuestionPriorityDetector:
    return CurrentQuestionPriorityDetector()


def test_pricing_question_has_priority(detector: CurrentQuestionPriorityDetector) -> None:
    result = detector.detect("How much does ghostwriting cost?")
    assert result.has_priority is True
    assert result.question_type == "pricing"
    assert result.should_answer_before_capture is True


def test_rough_estimate_has_priority(detector: CurrentQuestionPriorityDetector) -> None:
    result = detector.detect("Can you give me a rough range first before I share my details?")
    assert result.has_priority is True
    assert result.question_type == "rough_estimate"
    assert result.should_answer_before_capture is True


def test_distribution_question_has_priority(detector: CurrentQuestionPriorityDetector) -> None:
    result = detector.detect("Do you distribute on Amazon KDP?")
    assert result.has_priority is True
    assert result.question_type == "distribution"
    assert result.should_answer_before_capture is True


def test_christian_publishing_has_priority(detector: CurrentQuestionPriorityDetector) -> None:
    result = detector.detect("Have you worked with Christian publishers before?")
    assert result.has_priority is True
    assert result.question_type == "christian_publishing"


def test_fiverr_comparison_has_priority(detector: CurrentQuestionPriorityDetector) -> None:
    result = detector.detect("Why not just use Fiverr for this?")
    assert result.has_priority is True
    assert result.question_type == "fiverr_comparison"


def test_contact_refusal_has_priority(detector: CurrentQuestionPriorityDetector) -> None:
    result = detector.detect("I don't want to share my contact details before knowing the price.")
    assert result.has_priority is True
    assert result.question_type == "contact_refusal"
    assert result.should_answer_before_capture is True


def test_topic_correction_suppresses_old_path(detector: CurrentQuestionPriorityDetector) -> None:
    result = detector.detect("I was asking about distribution, not ghostwriting.")
    assert result.has_priority is True
    assert result.question_type == "topic_correction"
    assert result.suppress_old_sales_path is True


def test_process_question_has_priority(detector: CurrentQuestionPriorityDetector) -> None:
    result = detector.detect("How does the process work once I sign up?")
    assert result.has_priority is True
    assert result.question_type == "process"


def test_service_advice_has_priority(detector: CurrentQuestionPriorityDetector) -> None:
    result = detector.detect("Which service do you recommend for my situation?")
    assert result.has_priority is True
    assert result.question_type == "service_advice"


def test_plain_service_statement_no_priority(detector: CurrentQuestionPriorityDetector) -> None:
    result = detector.detect("I need editing for my fantasy novel.")
    assert result.has_priority is False
    assert result.question_type is None


def test_greeting_no_priority(detector: CurrentQuestionPriorityDetector) -> None:
    result = detector.detect("Hello, I'd like to learn more about your services.")
    assert result.has_priority is False


def test_no_priority_result_has_audit(detector: CurrentQuestionPriorityDetector) -> None:
    result = detector.detect("I need help with my book.")
    assert result.audit
    assert "no_priority_question" in result.audit


def test_priority_result_has_audit(detector: CurrentQuestionPriorityDetector) -> None:
    result = detector.detect("How much does editing cost?")
    assert result.has_priority is True
    assert any("matched:" in a for a in result.audit)
