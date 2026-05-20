"""Manuscript status v2 detector.

Canonical taxonomy (in priority order):
  published, completed, draft, partial_draft, in_progress,
  outline, voice_memo, journal_entries, rough_notes, idea

Legacy alias support:
  idea_only    → idea
  completed_draft → completed

False-positive guards:
  - Publishing-goal phrases ("I want it published") do NOT imply completed.
  - "book cover is complete" does NOT imply manuscript completed.
  - Negated and counterfactual statuses are not extracted.
  - Correction structures prefer the corrected (later) status.
"""

from __future__ import annotations

import re

from bookcraft.components.preprocessor.detectors.common import (
    iter_phrase_matches,
    match_is_counterfactual,
    match_is_negated,
)
from bookcraft.components.preprocessor.schemas import Span
from bookcraft.domain.enums import ManuscriptStatus

# ---------------------------------------------------------------------------
# False-positive guard patterns
# ---------------------------------------------------------------------------

# Publishing goal phrases do NOT imply the manuscript is finished.
_PUBLISHING_GOAL_RE = re.compile(
    r"\b(?:"
    # "want/need/... to publish"
    r"(?:want|need|would\s+like|looking\s+to|help\s+(?:me\s+)?(?:with\s+)?)"
    r"\s*(?:to\s+)?(?:publish(?:ing|ed)?|self.publish(?:ing|ed)?|"
    r"get\s+(?:it\s+)?published|publish\s+(?:my|the)\s+book)"
    r"|"
    # "help getting published" / "getting published" / "get published"
    r"(?:help\s+)?(?:getting|get)\s+published"
    r"|"
    # "self-publish" as a goal
    r"self.publish(?:ing)?"
    r")\b",
    re.IGNORECASE,
)
_PUBLISHING_INTENT_RE = re.compile(
    r"\b(?:can\s+you|do\s+you)\s+publish\b",
    re.IGNORECASE,
)

# "cover/design/artwork is complete" — NOT a manuscript status.
_COVER_COMPLETE_RE = re.compile(
    r"\b(?:cover|design|artwork|illustration)\s+(?:is\s+)?(?:complete|finished|done|ready)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Status phrase groups
# ---------------------------------------------------------------------------

_PUBLISHED_PHRASES = (
    "published",
    "already published",
    "book is published",
    "my book is out",
    "book is out",
)

_COMPLETED_PHRASES = (
    "complete manuscript",
    "completed manuscript",
    "finished manuscript",
    "finished my manuscript",
    "finished the manuscript",
    "manuscript is finished",
    "manuscript is complete",
    "manuscript is done",
    "manuscript finished",
    "ready manuscript",
    "manuscript is ready",
    "final draft",
    "manuscript is finalized",
    "manuscript finalized",
    "i've finished my manuscript",
    "i have finished my manuscript",
    "i have a finished manuscript",
    "i have completed my manuscript",
    "i completed my manuscript",
    "completed draft",
    "complete draft",
    "draft is complete",
    "draft is finished",
    "my book is complete",
    "my novel is complete",
    "my book is finished",
    "my novel is finished",
)

_DRAFT_PHRASES = (
    "rough draft",
    "first draft",
    "second draft",
    "have a draft",
    "i have a draft",
    "working draft",
    "just a draft",
    "only a draft",
)

_PARTIAL_DRAFT_PHRASES = (
    "partial draft",
    "partially written",
    "half written",
    "half-written",
    "120 pages done",
    "pages done",
    "chapters done",
    "some chapters",
    "few chapters",
    "a few chapters",
    "3 chapters",
    "three chapters",
    "several chapters",
    "started the draft",
    "started writing",
    "have some written",
)

_IN_PROGRESS_PHRASES = (
    "in progress",
    "still writing",
    "working on it",
    "currently writing",
    "actively writing",
)

_OUTLINE_PHRASES = (
    "chapter outline",
    "book outline",
    "detailed outline",
    "plot outline",
    "story outline",
    "have an outline",
    "written an outline",
    "outline ready",
    "outline is done",
    "outline is ready",
    "just an outline",
    "only an outline",
    "outline only",
    "outline",
)

_VOICE_MEMO_PHRASES = (
    "voice memo",
    "voice memos",
    "voice note",
    "voice notes",
    "audio notes",
    "recorded notes",
    "voice recording",
    "audio recording",
    "recordings",
)

_JOURNAL_ENTRIES_PHRASES = (
    "journal entries",
    "diary entries",
    "personal journal",
    "my journals",
    "journal",
    "diary",
)

_ROUGH_NOTES_PHRASES = (
    "rough notes",
    "scattered notes",
    "messy notes",
    "raw notes",
    "rough ideas",
    "scribbled notes",
    "just notes",
    "only notes",
    "notes only",
    "some notes",
)

_IDEA_PHRASES = (
    "story idea",
    "book idea",
    "just an idea",
    "only an idea",
    "it's just an idea",
    "have an idea",
    "concept for a book",
    "concept for a novel",
    "idea for a book",
    "idea for a novel",
    # legacy aliases
    "idea only",
    "only have an idea",
    "starting from scratch",
    "don't have time to write",
    "do not have time to write",
    "need someone to write it",
    "idea",
    "concept",
)

# Priority order (most → least specific).
_STATUS_PRIORITY: list[tuple[ManuscriptStatus, tuple[str, ...]]] = [
    (ManuscriptStatus.PUBLISHED, _PUBLISHED_PHRASES),
    (ManuscriptStatus.COMPLETED, _COMPLETED_PHRASES),
    (ManuscriptStatus.DRAFT, _DRAFT_PHRASES),
    (ManuscriptStatus.PARTIAL_DRAFT, _PARTIAL_DRAFT_PHRASES),
    (ManuscriptStatus.IN_PROGRESS, _IN_PROGRESS_PHRASES),
    (ManuscriptStatus.OUTLINE, _OUTLINE_PHRASES),
    (ManuscriptStatus.VOICE_MEMO, _VOICE_MEMO_PHRASES),
    (ManuscriptStatus.JOURNAL_ENTRIES, _JOURNAL_ENTRIES_PHRASES),
    (ManuscriptStatus.ROUGH_NOTES, _ROUGH_NOTES_PHRASES),
    (ManuscriptStatus.IDEA, _IDEA_PHRASES),
]

# Correction markers — when present, prefer the status that appears AFTER them.
_CORRECTION_RE = re.compile(
    r"\b(?:actually|but\s+(?:actually|now)|in\s+fact|turns\s+out|"
    r"(?:well|ok|okay)[,\s]+(?:actually|so)|i\s+mean|now\s+i\s+have)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_manuscript_status(
    text: str,
    negation_spans: list[Span] | None = None,
    counterfactual_spans: list[Span] | None = None,
) -> ManuscriptStatus | None:
    """Return the best manuscript status detected in text, or None."""
    publishing_goal = bool(_PUBLISHING_GOAL_RE.search(text) or _PUBLISHING_INTENT_RE.search(text))
    cover_complete = bool(_COVER_COMPLETE_RE.search(text))

    # If a correction marker is present, prefer the status in the post-correction text.
    correction_match = _CORRECTION_RE.search(text)
    if correction_match:
        after = text[correction_match.start() :]
        after_status = _first_match(
            after,
            negation_spans,
            counterfactual_spans,
            publishing_goal=publishing_goal,
            cover_complete=cover_complete,
        )
        if after_status is not None:
            return after_status

    return _first_match(
        text,
        negation_spans,
        counterfactual_spans,
        publishing_goal=publishing_goal,
        cover_complete=cover_complete,
    )


# ---------------------------------------------------------------------------
# Backward-compatibility helpers
# ---------------------------------------------------------------------------

LEGACY_STATUS_MAP: dict[str, ManuscriptStatus] = {
    "idea_only": ManuscriptStatus.IDEA,
    "completed_draft": ManuscriptStatus.COMPLETED,
}


def normalise_manuscript_status(raw: str) -> ManuscriptStatus | None:
    """Parse a raw status string to the canonical enum value."""
    if not raw:
        return None
    try:
        return ManuscriptStatus(raw)
    except ValueError:
        return LEGACY_STATUS_MAP.get(raw)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _first_match(
    text: str,
    negation_spans: list[Span] | None,
    counterfactual_spans: list[Span] | None,
    *,
    publishing_goal: bool = False,
    cover_complete: bool = False,
) -> ManuscriptStatus | None:
    for status, phrases in _STATUS_PRIORITY:
        # Guard both PUBLISHED and COMPLETED against publishing-goal context.
        if status in (ManuscriptStatus.COMPLETED, ManuscriptStatus.PUBLISHED):
            if publishing_goal and not _has_already_published_phrase(text):
                continue
        if status == ManuscriptStatus.COMPLETED:
            if cover_complete and not _has_manuscript_keyword(text):
                continue
        for match in iter_phrase_matches(text, phrases):
            if match_is_negated(text, match, negation_spans):
                continue
            if match_is_counterfactual(text, match, counterfactual_spans):
                continue
            return status
    return None


def _has_already_published_phrase(text: str) -> bool:
    """Return True when the text clearly states the book IS already published (not a goal)."""
    return bool(
        re.search(
            r"\b(?:already\s+published|book\s+is\s+(?:out|published)|"
            r"my\s+book\s+is\s+out|is\s+already\s+published)\b",
            text,
            re.IGNORECASE,
        )
    )


def _has_manuscript_keyword(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:manuscript|novel|book|story|writing|draft|chapter)\b",
            text,
            re.IGNORECASE,
        )
    )


# ---------------------------------------------------------------------------
# Kept for backward compatibility — old code imports these names.
# ---------------------------------------------------------------------------

PUBLISHED_MARKERS = _PUBLISHED_PHRASES
COMPLETED_MARKERS = _COMPLETED_PHRASES
PARTIAL_MARKERS = _PARTIAL_DRAFT_PHRASES
IDEA_MARKERS = _IDEA_PHRASES
