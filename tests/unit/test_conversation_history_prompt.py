"""Tests for Step 2: conversation history in the response LLM prompt."""

from __future__ import annotations

from bookcraft.components.response.generator import (
    _recent_turns_prompt_section,
    _truncate_on_word_boundary,
)


def test_recent_turns_none_returns_empty() -> None:
    assert _recent_turns_prompt_section(None) == ""


def test_recent_turns_empty_list_returns_empty() -> None:
    assert _recent_turns_prompt_section([]) == ""


def test_recent_turns_single_turn_included() -> None:
    result = _recent_turns_prompt_section(
        [("Tell me about ghostwriting.", "Happy to help — what stage is the manuscript at?")]
    )
    assert "Tell me about ghostwriting." in result
    assert "Happy to help" in result
    assert "Author:" in result
    assert "You:" in result


def test_recent_turns_prior_assistant_text_present() -> None:
    """The prior assistant text must appear in the section so LLM can relate to it."""
    turns = [("Hi there.", "Welcome! What book project are you working on?")]
    section = _recent_turns_prompt_section(turns)
    assert "Welcome!" in section
    assert "What book project" in section


def test_recent_turns_caps_at_three() -> None:
    """Only the last 3 turns are included — older turns are dropped."""
    turns = [
        ("turn 1 user", "turn 1 assistant"),
        ("turn 2 user", "turn 2 assistant"),
        ("turn 3 user", "turn 3 assistant"),
        ("turn 4 user", "turn 4 assistant"),
    ]
    section = _recent_turns_prompt_section(turns)
    assert "turn 4 user" in section
    assert "turn 3 user" in section
    assert "turn 2 user" in section
    assert "turn 1 user" not in section  # oldest turn dropped


def test_recent_turns_truncates_long_text() -> None:
    long_user = "A" * 400
    long_asst = "B" * 400
    section = _recent_turns_prompt_section([(long_user, long_asst)])
    # Each side should be truncated to ≤300 chars (plus "…")
    assert "A" * 400 not in section
    assert "B" * 400 not in section
    assert "…" in section


def test_recent_turns_instructs_no_repeat() -> None:
    section = _recent_turns_prompt_section([("hi", "hello")])
    assert "repeat" in section.lower() or "already asked" in section.lower()


def test_truncate_on_word_boundary_short_string() -> None:
    assert _truncate_on_word_boundary("Hello world", 100) == "Hello world"


def test_truncate_on_word_boundary_exact() -> None:
    text = "Hello world today"
    result = _truncate_on_word_boundary(text, 11)
    # Should contain "Hello" and end with ellipsis since text was cut
    assert "Hello" in result
    assert result.endswith("…")


def test_truncate_on_word_boundary_appends_ellipsis() -> None:
    result = _truncate_on_word_boundary("one two three four", 10)
    assert result.endswith("…")
    assert len(result) <= 11  # 10 + ellipsis char
