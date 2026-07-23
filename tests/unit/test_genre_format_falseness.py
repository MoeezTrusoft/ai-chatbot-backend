"""Regression guards for two MED-tier false-extraction bugs (BookCraft audit).

BUG 1 — genre_detector: bare "story"/"novel" fired the fiction rule on non-genre
        uses ("my life story", "long story short", "a novel approach").
BUG 2 — book_format: weak child cues ("school book", "for my son") inferred a
        children audience even in explicit adult context ("for adults", a
        professional/degree topic).

Each bug is covered from both sides: the false positives must now be suppressed,
and the true positives must still be detected.
"""

from __future__ import annotations

import pytest

from bookcraft.components.preprocessor.detectors.book_format import detect_book_format
from bookcraft.components.preprocessor.detectors.genre_detector import detect_genre


# ---------------------------------------------------------------------------
# BUG 1 — genre false positives now suppressed
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "my life story about cancer",          # "life story" idiom, not fiction
        "Long story short, I need editing",    # "long story short" idiom
        "a novel approach to marketing",       # "novel" as adjective
        "we took a novel approach",
        "to make a long story short",
        "the whole story of my childhood",
    ],
)
def test_bare_story_novel_idioms_are_not_fiction(text: str) -> None:
    assert detect_genre(text) != "fiction", text


# ---------------------------------------------------------------------------
# BUG 1 — genre true positives still detected
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("I'm writing a novel", "fiction"),
        ("it's a fantasy novel", "fantasy"),
        ("my story is a thriller", "thriller"),
        ("a science fiction story", "fiction"),
        ("I'm working on a novel", "fiction"),
    ],
)
def test_genuine_fiction_cues_still_detected(text: str, expected: str) -> None:
    assert detect_genre(text) == expected, text


def test_negation_guard_still_intact() -> None:
    # The pre-existing negation fix must survive these changes.
    assert detect_genre("it's not a children's book") is None
    assert detect_genre("I don't want a thriller") is None


# ---------------------------------------------------------------------------
# BUG 2 — book_format audience false positives now suppressed
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "a school book about tax law for adults",
        "for my son's business degree",
        "a school book for a university law course",
        "writing for my daughter's MBA dissertation",
    ],
)
def test_weak_child_cue_suppressed_by_adult_context(text: str) -> None:
    assert detect_book_format(text).audience is None, text


# ---------------------------------------------------------------------------
# BUG 2 — book_format children true positives still detected
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "a picture book for my 5-year-old",
        "a children's book",
        "bedtime stories for kids",
        "a story for toddlers",
        "a book for young readers",
        "for children ages 3-6",
    ],
)
def test_genuine_child_audience_still_detected(text: str) -> None:
    assert detect_book_format(text).audience == "children", text


def test_picture_book_with_young_age_sets_format_and_children() -> None:
    result = detect_book_format("a picture book for my 5-year-old")
    assert "picture_book" in result.book_formats
    assert result.audience == "children"


def test_weak_child_cue_alone_still_infers_children() -> None:
    # Without adult context, a weak cue is still allowed to infer children.
    assert detect_book_format("a school book with a fun dragon").audience == "children"
