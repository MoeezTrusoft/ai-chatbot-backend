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
    # "want/need/looking to publish / get published / get my book published"
    r"(?:want|need|would\s+like|looking\s+to|help\s+(?:me\s+)?(?:with\s+)?)"
    r"\s*(?:to\s+)?(?:publish(?:ing|ed)?|self.publish(?:ing|ed)?|"
    r"get\s+(?:it\s+|(?:my|the|a)\s+(?:book|novel|manuscript|journal|story|work)\s+)?published|"
    r"publish\s+(?:my|the|a)\s+(?:book|novel|manuscript|journal|story))"
    r"|"
    # "help getting published" / "getting published" / "get published"
    r"(?:help\s+)?(?:getting|get)\s+published"
    r"|"
    # "get my book/journal/novel published" (no preceding want/looking to)
    r"get\s+(?:it|(?:my|the|a)\s+(?:book|novel|manuscript|journal|story|work))\s+published"
    r"|"
    # "make it published" / "make my book published"
    r"make\s+(?:it|my\s+\w+|the\s+\w+)\s+published"
    r"|"
    # "want to publish it" / "want to make it published"
    r"want\s+to\s+(?:make\s+it\s+published|publish\s+it)"
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
# Weak bare-word guards
# ---------------------------------------------------------------------------
# Single-word status cues that ALSO occur in ordinary, non-manuscript usage
# ("let me outline what I need", "proof of concept", "I keep a journal", "notes on
# our call"). These are only accepted as a manuscript status when the message is
# genuinely about the book/manuscript — never when used as a verb or as a fixed
# idiom about something else. Matching is done on the (whitespace-normalised) matched
# text, so richer phrases like "just an outline" / "concept for a novel" are unaffected.
_WEAK_BARE_PHRASES = frozenset(
    {"outline", "concept", "idea", "journal", "diary", "some notes"}
)

# "outline" used as a VERB ("let me outline …", "to outline …"), not a book outline.
_OUTLINE_VERB_PREFIX_RE = re.compile(
    r"(?:\b(?:let'?s|let\s+me|to|i'?ll|i\s+will|we'?ll|we\s+will|"
    r"can\s+you|could\s+you|would\s+you|please|help\s+(?:me|you)|"
    r"gonna|going\s+to|want(?:ing)?\s+to|need(?:ing)?\s+to|will|should)\b)\W*$",
    re.IGNORECASE,
)
# "proof of concept" — fixed idiom, not a book idea.
_CONCEPT_IDIOM_PREFIX_RE = re.compile(r"\bproof\s+of\s*$", re.IGNORECASE)
# "keep a journal / diary" — a habit, not the manuscript form.
_JOURNAL_HABIT_PREFIX_RE = re.compile(
    r"\b(?:keep|keeps|keeping|kept|write|writing|wrote)\s+(?:in\s+)?(?:a|my|the)?\s*$",
    re.IGNORECASE,
)
# "notes on / about <X>" ("notes on our call") — notes ABOUT something, not the form.
_NOTES_ABOUT_SUFFIX_RE = re.compile(
    r"^\s+(?:on|about|from|regarding|re)\b", re.IGNORECASE
)
# Stage-minimiser / possessive lead-in that marks a genuine "the manuscript is only at
# <stage>" statement ("it's more of a concept", "I just have a concept").
_WEAK_STAGE_DETERMINER_RE = re.compile(
    r"\b(?:just|only|merely|simply|barely|more\s+of|"
    r"(?:i\s+)?(?:have|had|got)|there'?s|it'?s|its)\b"
    r"\s*(?:(?:only|just|a|an|the|my|some)\s+)*$",
    re.IGNORECASE,
)
# "in progress on the <non-book noun>" — the status modifies a different noun.
_IN_PROGRESS_ON_RE = re.compile(
    r"^\s+on\s+(?:the|a|an|my|our|this|that|their|his|her)?\s*([\w\s-]+)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Status phrase groups
# ---------------------------------------------------------------------------

# PUBLISHED = the book is actually distributed / for sale. "Ready to publish" and
# "KDP-ready" are NOT published — they mean the files are finalized (COMPLETED). Only
# genuine distribution/upload evidence counts here (chat: "ready to publish ... KDP
# ready" was wrongly stored as published, derailing the whole flow).
_PUBLISHED_PHRASES = (
    "published",
    "already published",
    "book is published",
    "my book is out",
    "book is out",
    "uploaded it to kdp",
    "uploaded to kdp",
    "it's live on amazon",
    "live on amazon",
    "on amazon already",
    "for sale on amazon",
    "for sale on kindle",
    "available on amazon",
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
    "full draft",  # Phase 9 hotfix: "85,000 words, full draft"
    "fully drafted",
    "complete draft",
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
    # Ready-to-publish / print-ready signals: the interior + cover files are finalized
    # and ready to go out, but the book is NOT yet distributed — that is COMPLETED, not
    # PUBLISHED. ("ready to publish ... KDP ready" must never read as already published.)
    "kdp ready",
    "kdp-ready",
    "ready for kdp",
    "print ready",
    "print-ready",
    "proof copy",
    "publication ready",
    "publication-ready",
    "ready for publication",
    "fully formatted",
    "formatted and edited",
    "formatted and ready",
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
        # PUBLISHED under a publishing GOAL needs real distribution evidence — "I want
        # to publish" must never read as already published.
        if status == ManuscriptStatus.PUBLISHED:
            if publishing_goal and not _has_already_published_phrase(text):
                continue
        if status == ManuscriptStatus.COMPLETED:
            if cover_complete and not _has_manuscript_keyword(text):
                continue
            # A publishing goal ("I want to publish my finished/KDP-ready manuscript")
            # does NOT cancel a genuine completion/readiness signal — only suppress
            # COMPLETED when the text is a bare goal with no completion evidence.
            if publishing_goal and not (
                _has_already_published_phrase(text) or _has_completion_readiness(text)
            ):
                continue
        for match in iter_phrase_matches(text, phrases):
            if match_is_negated(text, match, negation_spans):
                continue
            if match_is_counterfactual(text, match, counterfactual_spans):
                continue
            # "in progress on the marketing plan" modifies a non-book noun — the book
            # status (e.g. "just an idea") in another clause should win.
            if status == ManuscriptStatus.IN_PROGRESS and _modifies_nonbook_noun(text, match):
                continue
            # Weak single-word cues need genuine manuscript context / must not be a
            # verb or a fixed idiom about something else.
            if _is_weak_bare_match(match) and not _weak_bare_is_manuscript(text, match):
                continue
            return status
    return None


def _norm_match(match: re.Match[str]) -> str:
    return re.sub(r"\s+", " ", match.group()).strip().casefold()


def _is_weak_bare_match(match: re.Match[str]) -> bool:
    return _norm_match(match) in _WEAK_BARE_PHRASES


def _weak_bare_is_manuscript(text: str, match: re.Match[str]) -> bool:
    """Decide whether a weak single-word status cue really denotes a manuscript stage."""
    matched = _norm_match(match)
    prefix = text[: match.start()]
    suffix = text[match.end() :]

    # Verb / idiom usages are never a manuscript status.
    if matched == "outline" and _OUTLINE_VERB_PREFIX_RE.search(prefix):
        return False
    if matched == "concept" and _CONCEPT_IDIOM_PREFIX_RE.search(prefix):
        return False
    if matched in ("journal", "diary") and _JOURNAL_HABIT_PREFIX_RE.search(prefix):
        return False
    if matched == "some notes" and _NOTES_ABOUT_SUFFIX_RE.search(suffix):
        return False

    # Otherwise require real manuscript context: a book keyword, an explicit
    # stage-minimiser lead-in, or a short bare answer to a stage question.
    if _has_manuscript_keyword(text):
        return True
    if _WEAK_STAGE_DETERMINER_RE.search(prefix):
        return True
    return _is_short_bare_answer(text)


def _is_short_bare_answer(text: str) -> bool:
    return len(re.findall(r"[A-Za-z']+", text)) <= 3


def _modifies_nonbook_noun(text: str, match: re.Match[str]) -> bool:
    """True when an in-progress cue is scoped to a non-book noun ("… on the plan")."""
    m = _IN_PROGRESS_ON_RE.match(text[match.end() :])
    if not m:
        return False
    obj = re.split(r"[.,;:!?]", m.group(1))[0]
    return not _has_manuscript_keyword(obj)


def _has_already_published_phrase(text: str) -> bool:
    """Return True when the text clearly states the book IS already DISTRIBUTED.

    Only genuine publication evidence (live/for sale/uploaded to KDP) — NOT mere
    readiness signals like "KDP-ready" or "print-ready", which mean the files are
    finalized (COMPLETED) but the book is not yet published.
    """
    return bool(
        re.search(
            r"\b(?:already\s+published|book\s+is\s+(?:out|published)|"
            r"my\s+book\s+is\s+out|is\s+already\s+published|"
            r"(?:it'?s\s+)?live\s+on\s+amazon|for\s+sale\s+on\s+\w+|"
            r"available\s+on\s+amazon|"
            r"uploaded\s+(?:it\s+)?to\s+kdp)\b",
            text,
            re.IGNORECASE,
        )
    )


def _has_completion_readiness(text: str) -> bool:
    """Return True when the text carries a genuine 'manuscript is finished / ready to
    publish' signal (finalized files), so a co-occurring publishing GOAL does not
    wrongly suppress COMPLETED."""
    return bool(
        re.search(
            r"\b(?:kdp[-\s]?ready|print[-\s]?ready|ready\s+for\s+kdp|proof\s+copy|"
            r"publication[-\s]?ready|ready\s+for\s+publication|fully\s+formatted|"
            r"formatted\s+and\s+(?:edited|ready)|final\s+draft|fully\s+drafted|"
            r"finished\s+(?:my\s+|the\s+)?manuscript|complete(?:d)?\s+manuscript|"
            r"manuscript\s+is\s+(?:complete|finished|done|ready|finalized))\b",
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
