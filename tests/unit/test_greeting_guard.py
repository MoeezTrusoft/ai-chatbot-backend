"""Tests for GreetingIntentGuard."""

from __future__ import annotations

import pytest

from bookcraft.components.preprocessor.detectors.greeting import detect_greeting_only


@pytest.mark.parametrize(
    "text",
    [
        "hello",
        "hi",
        "hey",
        "hello there",
        "hello mate",
        "good morning",
        "good afternoon",
        "salam",
        "Hi there!",
        "Hello!",
        "hey there",
    ],
)
def test_hello_is_greeting_only(text: str) -> None:
    result = detect_greeting_only(text)
    assert result.is_greeting_only is True


@pytest.mark.parametrize(
    "text",
    [
        "hello, I want to write a book",
        "hi, can you tell me the price",
        "hey I need editing for my manuscript",
        "hello what services do you offer for fiction",
        "hi can you give me a word count estimate",
    ],
)
def test_hello_with_content_is_not_greeting_only(text: str) -> None:
    result = detect_greeting_only(text)
    assert result.is_greeting_only is False


def test_hello_mate_does_not_request_word_count() -> None:
    result = detect_greeting_only("hello mate")
    assert result.is_greeting_only is True
    # The audit should NOT contain any scoping reference.
    audit_str = " ".join(result.audit)
    assert "word_count" not in audit_str
    assert "genre" not in audit_str


def test_greeting_only_has_audit() -> None:
    result = detect_greeting_only("hello")
    assert result.audit
    assert any("greeting" in entry for entry in result.audit)


def test_service_question_is_not_greeting() -> None:
    result = detect_greeting_only("What services do you offer?")
    assert result.is_greeting_only is False
