"""Tests for the v2 manuscript status taxonomy."""

from __future__ import annotations

from bookcraft.components.preprocessor.detectors.manuscript_status_detector import (
    detect_manuscript_status,
)
from bookcraft.domain.enums import ManuscriptStatus


def _detect(text: str) -> ManuscriptStatus | None:
    return detect_manuscript_status(text)


def test_extracts_idea() -> None:
    assert _detect("I just have an idea for a book.") == ManuscriptStatus.IDEA
    assert _detect("It's just an idea, nothing written yet.") == ManuscriptStatus.IDEA
    assert _detect("I have a concept for a novel.") == ManuscriptStatus.IDEA


def test_extracts_rough_notes() -> None:
    assert _detect("I have rough notes scattered everywhere.") == ManuscriptStatus.ROUGH_NOTES
    assert _detect("Just some messy notes so far.") == ManuscriptStatus.ROUGH_NOTES
    assert _detect("I have raw notes for the story.") == ManuscriptStatus.ROUGH_NOTES


def test_extracts_journal_entries() -> None:
    assert _detect("My book is based on journal entries.") == ManuscriptStatus.JOURNAL_ENTRIES
    assert _detect("I have diary entries from my life.") == ManuscriptStatus.JOURNAL_ENTRIES
    assert (
        _detect("It's a personal journal I want to turn into a book.")
        == ManuscriptStatus.JOURNAL_ENTRIES
    )


def test_extracts_voice_memo() -> None:
    assert _detect("I have voice memos recorded.") == ManuscriptStatus.VOICE_MEMO
    assert _detect("The story is in audio recordings.") == ManuscriptStatus.VOICE_MEMO
    assert _detect("I've been making voice notes about the plot.") == ManuscriptStatus.VOICE_MEMO


def test_extracts_outline() -> None:
    assert _detect("I have a detailed outline ready.") == ManuscriptStatus.OUTLINE
    assert _detect("I've written a chapter outline.") == ManuscriptStatus.OUTLINE
    assert _detect("My book outline is done.") == ManuscriptStatus.OUTLINE


def test_extracts_in_progress() -> None:
    assert _detect("The manuscript is in progress.") == ManuscriptStatus.IN_PROGRESS
    assert _detect("I'm still writing the book.") == ManuscriptStatus.IN_PROGRESS
    assert _detect("I'm currently writing it.") == ManuscriptStatus.IN_PROGRESS


def test_extracts_partial_draft_from_chapters() -> None:
    assert _detect("I have 3 chapters done.") == ManuscriptStatus.PARTIAL_DRAFT
    assert _detect("I have a partial draft.") == ManuscriptStatus.PARTIAL_DRAFT
    assert _detect("The book is half written.") == ManuscriptStatus.PARTIAL_DRAFT
    assert _detect("I have some chapters written.") == ManuscriptStatus.PARTIAL_DRAFT


def test_extracts_draft() -> None:
    assert _detect("I have a first draft.") == ManuscriptStatus.DRAFT
    assert _detect("I've got a rough draft.") == ManuscriptStatus.DRAFT
    assert _detect("I have a working draft to edit.") == ManuscriptStatus.DRAFT


def test_extracts_completed() -> None:
    assert _detect("My manuscript is complete.") == ManuscriptStatus.COMPLETED
    assert _detect("I have a finished manuscript.") == ManuscriptStatus.COMPLETED
    assert _detect("The manuscript is done.") == ManuscriptStatus.COMPLETED
    assert _detect("I have a final draft.") == ManuscriptStatus.COMPLETED


def test_publishing_goal_does_not_imply_completed() -> None:
    # "want/need to publish" should NOT be extracted as completed status.
    assert _detect("I want to publish my book.") is None
    assert _detect("I need publishing help.") is None
    assert _detect("Can you publish my book?") is None
    assert _detect("I need help getting published.") is None


def test_negated_status_not_extracted() -> None:
    # "not completed", "not finished" should not produce COMPLETED.
    result = _detect("My manuscript is not finished.")
    assert result != ManuscriptStatus.COMPLETED
    # "don't have a draft" should not produce DRAFT.
    result2 = _detect("I don't have a draft yet.")
    assert result2 != ManuscriptStatus.DRAFT


def test_correction_prefers_latest_specific_status() -> None:
    # "I thought it was complete, actually only an outline" → outline
    result = _detect("I thought it was complete, actually only an outline.")
    assert result == ManuscriptStatus.OUTLINE

    # "I had notes, now I have a full draft" → draft
    result2 = _detect("I had notes, now I have a first draft.")
    assert result2 == ManuscriptStatus.DRAFT


def test_cover_complete_does_not_imply_manuscript_completed() -> None:
    result = _detect("The cover design is complete.")
    assert result != ManuscriptStatus.COMPLETED

    result2 = _detect("My book cover is finished and ready.")
    assert result2 != ManuscriptStatus.COMPLETED


def test_legacy_aliases_still_detected() -> None:
    # Phrases that previously produced IDEA_ONLY / COMPLETED_DRAFT should
    # still be detected (now as IDEA / COMPLETED).
    assert _detect("idea only") == ManuscriptStatus.IDEA
    assert _detect("I have a completed draft.") == ManuscriptStatus.COMPLETED
