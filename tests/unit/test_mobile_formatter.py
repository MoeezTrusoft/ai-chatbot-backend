"""Hard mobile reply limits in the formatter (rule M-02) and the one-question rule (M-03)."""
from __future__ import annotations

from bookcraft.components.response.formatter import (
    ResponseFormatter,
    pack_sentences,
    strip_questions,
)

LONG = (
    "Editing a finished thriller usually starts with a manuscript review so the editor can see "
    "which level of editing it needs. From there we match you with an editor who works in your "
    "genre. The editor returns a marked up manuscript with notes on pacing and plot. Would you "
    "like to know how the stages differ? I can also explain what proofreading adds at the end."
)


def test_mobile_reply_is_capped_at_two_bubbles_of_200_characters():
    bubbles = ResponseFormatter().format(LONG, max_chars=200, max_bubbles=2)
    assert len(bubbles) == 2
    assert all(len(bubble.text) <= 200 for bubble in bubbles)
    assert [bubble.bubble_index for bubble in bubbles] == [0, 1]


def test_the_cut_falls_on_a_sentence_boundary():
    bubbles = ResponseFormatter().format(LONG, max_chars=200, max_bubbles=1)
    assert len(bubbles) == 1
    assert bubbles[0].text.endswith(".")
    assert LONG.startswith(bubbles[0].text)


def test_a_short_reply_stays_in_one_bubble():
    bubbles = ResponseFormatter().format("Yes, we edit thrillers.", max_chars=200, max_bubbles=2)
    assert [bubble.text for bubble in bubbles] == ["Yes, we edit thrillers."]


def test_a_sentence_longer_than_the_limit_is_split_on_words():
    text = " ".join(["manuscript"] * 60) + "."
    chunks = pack_sentences(text, limit=200, max_bubbles=2)
    assert len(chunks) == 2
    assert all(len(chunk) <= 200 for chunk in chunks)
    assert not chunks[0].endswith(" ")


def test_desktop_formatting_is_unchanged_without_limits():
    formatter = ResponseFormatter()
    assert len(formatter.format(LONG)) == 1  # under the 500 character desktop split
    paragraphs = formatter.format("First paragraph.\n\nSecond paragraph.")
    assert [bubble.text for bubble in paragraphs] == ["First paragraph.", "Second paragraph."]


def test_strip_questions_removes_only_question_sentences():
    assert strip_questions("We edit thrillers. Would you like a quote? It takes two steps.") == (
        "We edit thrillers. It takes two steps."
    )
    assert strip_questions("What genre is it?") == ""


def test_email_segments_survive_mobile_packing():
    bubbles = ResponseFormatter().format(
        "You can reach the team at hello@example.com any time.", max_chars=200, max_bubbles=2
    )
    assert bubbles[0].rich_segments == [{"type": "email", "text": "hello@example.com"}]
