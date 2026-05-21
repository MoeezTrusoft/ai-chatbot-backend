"""Tests for BookFormatDetector."""

from __future__ import annotations

import pytest

from bookcraft.components.preprocessor.detectors.book_format import detect_book_format


def test_picture_book_sets_format_not_children_genre() -> None:
    result = detect_book_format("I want to create a picture book.")
    assert "picture_book" in result.book_formats
    # Audience must not be inferred from "picture book" alone.
    assert result.audience is None


def test_picture_book_for_kids_sets_audience_children() -> None:
    result = detect_book_format("I want to create a picture book for kids.")
    assert "picture_book" in result.book_formats
    assert result.audience == "children"


def test_picture_book_for_children_sets_audience() -> None:
    result = detect_book_format("I'm working on a picture book for children ages 3-6.")
    assert "picture_book" in result.book_formats
    assert result.audience == "children"


def test_no_picture_book_no_format() -> None:
    result = detect_book_format("I want to write a thriller novel.")
    assert "picture_book" not in result.book_formats
    assert result.audience is None


def test_board_book_sets_format() -> None:
    result = detect_book_format("I want to make a board book.")
    assert "board_book" in result.book_formats


def test_graphic_novel_sets_format() -> None:
    result = detect_book_format("This is a graphic novel I have planned.")
    assert "graphic_novel" in result.book_formats


def test_chapter_book_sets_format() -> None:
    result = detect_book_format("It will be a chapter book.")
    assert "chapter_book" in result.book_formats


def test_bedtime_story_sets_children_audience() -> None:
    result = detect_book_format("This is a bedtime story for toddlers.")
    assert result.audience == "children"


def test_no_children_audience_without_cue() -> None:
    result = detect_book_format("I need a picture book about a dragon adventure.")
    assert result.audience is None


@pytest.mark.parametrize(
    "text",
    [
        "I want to create a picture book",
        "it is a picture book",
        "working on a picture book project",
    ],
)
def test_picture_book_alone_never_sets_children_genre(text: str) -> None:
    result = detect_book_format(text)
    assert "picture_book" in result.book_formats
    assert result.audience is None
