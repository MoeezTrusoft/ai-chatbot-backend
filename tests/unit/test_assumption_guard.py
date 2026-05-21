"""Tests for AssumptionGuard."""

from __future__ import annotations

import pytest

from bookcraft.components.context.assumption_guard import AssumptionGuard
from bookcraft.components.context.schemas import ContextPack, KnownFact
from bookcraft.components.response.planner import ResponsePlan


@pytest.fixture
def guard() -> AssumptionGuard:
    return AssumptionGuard()


def _pack_with_genre(genre: str, confidence: float = 0.9) -> ContextPack:
    return ContextPack(
        known_facts=[
            KnownFact(
                path="project.genre",
                label="genre",
                value=genre,
                confidence=confidence,
                source="user_stated",
            )
        ]
    )


def _empty_pack() -> ContextPack:
    return ContextPack()


def test_uncertain_genre_delta_is_blocked(guard: AssumptionGuard) -> None:
    result = guard.evaluate_delta(
        fact_path="project.genre",
        candidate_value="memoir",
        genre_status="uncertain",
    )
    assert result.certainty == "uncertain"


def test_confirmed_genre_delta_allowed(guard: AssumptionGuard) -> None:
    pack = _pack_with_genre("memoir")
    result = guard.evaluate_delta(
        fact_path="project.genre",
        candidate_value="memoir",
        context_pack=pack,
    )
    assert result.certainty == "confirmed"


def test_negated_genre_delta_blocked(guard: AssumptionGuard) -> None:
    result = guard.evaluate_delta(
        fact_path="project.genre",
        candidate_value="memoir",
        genre_candidates=["business"],  # memoir not in candidates
        genre_status=None,
    )
    # genre not confirmed in context → unknown
    assert result.certainty in {"unknown", "candidate", "uncertain"}


def test_response_claim_established_fact_without_confirmation_fails_quality(
    guard: AssumptionGuard,
) -> None:
    failures = guard.check_response(
        text="We've established that this is your memoir project.",
        context_pack=_empty_pack(),
    )
    assert any("assumption_leak" in f for f in failures)


def test_response_with_confirmed_genre_does_not_fail(guard: AssumptionGuard) -> None:
    pack = _pack_with_genre("memoir")
    failures = guard.check_response(
        text="Since you're writing a memoir, ghostwriting is well-suited for your project.",
        context_pack=pack,
    )
    # "since you're writing a memoir" should not fire when genre IS confirmed.
    assumption_fails = [f for f in failures if "assumption_leak:established" in f]
    assert not assumption_fails


def test_greeting_scoping_violation_detected(guard: AssumptionGuard) -> None:
    plan = ResponsePlan(primary_goal="greeting_welcome")
    failures = guard.check_response(
        text="Hi there! What word count are you working with?",
        context_pack=_empty_pack(),
        response_plan=plan,
    )
    assert any("greeting_asked_scoping" in f for f in failures)


def test_greeting_welcome_response_passes(guard: AssumptionGuard) -> None:
    plan = ResponsePlan(primary_goal="greeting_welcome")
    failures = guard.check_response(
        text="Welcome to BookCraft! What can I help you with today?",
        context_pack=_empty_pack(),
        response_plan=plan,
    )
    scoping_fails = [f for f in failures if "greeting_asked_scoping" in f]
    assert not scoping_fails


def test_picture_book_assumed_children_without_audience_fails(guard: AssumptionGuard) -> None:
    pack = ContextPack(book_formats=["picture_book"])
    failures = guard.check_response(
        text="For your children's picture book, we recommend illustration services.",
        context_pack=pack,
    )
    assert any("picture_book_assumed_children_genre" in f for f in failures)


def test_picture_book_with_audience_confirmed_does_not_fail(guard: AssumptionGuard) -> None:
    pack = ContextPack(book_formats=["picture_book"], audience="children")
    failures = guard.check_response(
        text="For your children's picture book, we recommend illustration services.",
        context_pack=pack,
    )
    pb_fails = [f for f in failures if "picture_book_assumed_children_genre" in f]
    assert not pb_fails
