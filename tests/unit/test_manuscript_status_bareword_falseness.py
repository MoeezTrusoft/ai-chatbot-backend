"""Regression: weak bare single-word status cues must not over-match.

Bare entries in the phrase groups ("outline", "concept", "idea", "journal",
"diary", "some notes") also occur in everyday, non-manuscript usage — as verbs,
fixed idioms, or in reference to something other than the book. These were the
MED-tier false manuscript_status extractions found in the BookCraft audit.

The detector now only accepts a weak bare cue as a manuscript status when the
message is genuinely about the book/manuscript (a book keyword, a stage-minimiser
lead-in, or a short bare answer) and never when it is a verb / fixed idiom about
something else. Richer phrases ("just an outline", "concept for a novel", …) are
unaffected. See manuscript_status_detector.py.
"""

from __future__ import annotations

import pytest

from bookcraft.components.preprocessor.detectors.manuscript_status_detector import (
    detect_manuscript_status,
)
from bookcraft.domain.enums import ManuscriptStatus


def _detect(text: str) -> ManuscriptStatus | None:
    return detect_manuscript_status(text)


# ---------------------------------------------------------------------------
# False positives — must be suppressed.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # "outline" used as a verb, not a manuscript outline.
        "Let me outline what I need",
        "I'll outline my thoughts and get back to you",
        "Can you outline the next steps?",
        "To outline the process, first we talk.",
        # "proof of concept" — fixed idiom, not a book idea.
        "proof of concept",
        "we built a proof of concept",
        # "journal"/"diary" as a habit, not the manuscript form.
        "I keep a journal",
        "I write in a journal every night",
        # "notes on/about <X>" — notes about something else.
        "took some notes on our call",
        "some notes about the meeting",
    ],
)
def test_weak_bare_words_do_not_over_match(text: str) -> None:
    assert _detect(text) is None


def test_in_progress_scoped_to_nonbook_yields_book_status() -> None:
    # "in progress" modifies "the marketing plan"; the book clause ("just an idea")
    # is the real manuscript status.
    assert (
        _detect("in progress on the marketing plan, book is just an idea")
        == ManuscriptStatus.IDEA
    )


def test_in_progress_scoped_to_nonbook_alone_is_not_a_status() -> None:
    assert _detect("still in progress on the website redesign") is None


# ---------------------------------------------------------------------------
# True positives — must be preserved.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("just an outline", ManuscriptStatus.OUTLINE),
        ("I only have an outline", ManuscriptStatus.OUTLINE),
        ("I have a detailed outline ready.", ManuscriptStatus.OUTLINE),
        ("My book outline is done.", ManuscriptStatus.OUTLINE),
        ("it's just an idea", ManuscriptStatus.IDEA),
        ("I only have an idea.", ManuscriptStatus.IDEA),
        ("I have a concept for a novel.", ManuscriptStatus.IDEA),
        ("it's more of a concept right now", ManuscriptStatus.IDEA),
        ("I have journal entries", ManuscriptStatus.JOURNAL_ENTRIES),
        ("It's a personal journal I want to turn into a book.", ManuscriptStatus.JOURNAL_ENTRIES),
        ("I have diary entries from my life.", ManuscriptStatus.JOURNAL_ENTRIES),
        ("rough notes", ManuscriptStatus.ROUGH_NOTES),
        ("I have some notes for my book", ManuscriptStatus.ROUGH_NOTES),
        ("in progress", ManuscriptStatus.IN_PROGRESS),
        ("still writing", ManuscriptStatus.IN_PROGRESS),
        ("in progress on my novel", ManuscriptStatus.IN_PROGRESS),
    ],
)
def test_true_positives_preserved(text: str, expected: ManuscriptStatus) -> None:
    assert _detect(text) == expected
