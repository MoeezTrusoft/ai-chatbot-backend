"""Tests for GenreUncertaintyDetector."""

from __future__ import annotations

import pytest

from bookcraft.components.preprocessor.detectors.genre_uncertainty import detect_genre_uncertainty


@pytest.mark.parametrize(
    "text",
    [
        "I don't know if it should be fiction, memoir, or business book",
        "not sure whether it's fiction or memoir",
        "I'm not sure — maybe memoir or business",
        "could be fiction, could be self-help",
        "between memoir and business, still deciding",
        "maybe memoir or business",
        "I haven't decided if it's fiction or memoir",
        "I don't know whether this is fiction or a memoir",
    ],
)
def test_dont_know_fiction_memoir_business_creates_candidates(text: str) -> None:
    result = detect_genre_uncertainty(text)
    assert result.uncertain is True
    assert len(result.genre_candidates) >= 1


def test_maybe_memoir_or_business_uncertain() -> None:
    result = detect_genre_uncertainty("maybe memoir or business")
    assert result.uncertain is True
    assert "memoir" in result.genre_candidates
    assert "business" in result.genre_candidates


def test_dont_want_fiction_is_negated_not_confirmed() -> None:
    result = detect_genre_uncertainty("I don't want fiction, maybe memoir")
    assert result.uncertain is True
    # fiction should be in negated, not candidates
    assert "fiction" in result.negated_genres
    assert "fiction" not in result.genre_candidates
    assert "memoir" in result.genre_candidates


def test_clear_genre_statement_is_not_uncertain() -> None:
    result = detect_genre_uncertainty("I am writing a memoir about my childhood.")
    assert result.uncertain is False
    assert result.genre_candidates == []


def test_uncertainty_result_has_audit() -> None:
    result = detect_genre_uncertainty("not sure if fiction or memoir")
    assert result.audit
    assert any("uncertainty_cue" in entry for entry in result.audit)


def test_no_genre_words_returns_empty_candidates() -> None:
    result = detect_genre_uncertainty("I'm not sure what I want to write about yet")
    assert result.uncertain is True
    assert result.genre_candidates == []
