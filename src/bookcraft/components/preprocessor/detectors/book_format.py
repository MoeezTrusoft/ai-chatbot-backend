"""BookFormatDetector — detects book format/type without assuming genre or audience."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

_PICTURE_BOOK_RE = re.compile(r"\bpicture\s+book\b", flags=re.IGNORECASE)
_BOARD_BOOK_RE = re.compile(r"\bboard\s+book\b", flags=re.IGNORECASE)
_CHAPTER_BOOK_RE = re.compile(r"\bchapter\s+book\b", flags=re.IGNORECASE)
_GRAPHIC_NOVEL_RE = re.compile(r"\bgraphic\s+novel\b", flags=re.IGNORECASE)
_ILLUSTRATED_BOOK_RE = re.compile(r"\billustrated\s+book\b", flags=re.IGNORECASE)

# STRONG child-audience cues — unambiguous, always infer a children audience.
# A young-child age (0-12) counts; "45-year-old" / "ages 30" deliberately do not.
_STRONG_CHILD_RE = re.compile(
    r"\b(?:children|kids?|toddlers?|preschool|kindergarten|young\s+readers?|"
    r"bedtime\s+stor(?:y|ies))\b"
    r"|\bages?\s+(?:[0-9]|1[0-2])\b"
    r"|\b(?:[0-9]|1[0-2])[-\s]?year[-\s]?olds?\b",
    flags=re.IGNORECASE,
)

# WEAK child-audience cues — suggestive but easily adult ("school book" can be a
# college text, a "son" can be a grown professional). Only infer children from
# these when no explicit adult context is present.
_WEAK_CHILD_RE = re.compile(
    r"\bschool\s+book\b|\belementary\b|\bjuvenile\b|"
    r"\bfor\s+(?:my\s+)?(?:child|kid|daughter|son)\b",
    flags=re.IGNORECASE,
)

# Explicit adult context — suppresses inference from WEAK cues only. Covers
# stated adult audience and professional/academic subject matter.
_ADULT_CONTEXT_RE = re.compile(
    r"\bfor\s+adults?\b|\badults?\b|\bgrown[-\s]?ups?\b|"
    r"\b(?:business|law|legal|tax|finance|financial|accounting|medical|academic|"
    r"professional|corporate|marketing|management|economics|engineering|"
    r"scientific|technical)\b"
    r"|\b(?:degree|mba|phd|doctoral|dissertation|thesis|college|university|"
    r"graduate|undergraduate)\b",
    flags=re.IGNORECASE,
)


class BookFormatResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_formats: list[str] = Field(default_factory=list)
    audience: str | None = None
    audit: list[str] = Field(default_factory=list)


def detect_book_format(text: str) -> BookFormatResult:
    """
    Detect book format/type from text.

    'picture book' is a format, NOT automatically a children's book or children's genre.
    Children audience is only inferred when explicit child-audience cues appear alongside.
    """
    audit: list[str] = []
    formats: list[str] = []
    audience: str | None = None

    if _PICTURE_BOOK_RE.search(text):
        formats.append("picture_book")
        audit.append("format_detected:picture_book")

    if _BOARD_BOOK_RE.search(text):
        formats.append("board_book")
        audit.append("format_detected:board_book")

    if _CHAPTER_BOOK_RE.search(text):
        formats.append("chapter_book")
        audit.append("format_detected:chapter_book")

    if _GRAPHIC_NOVEL_RE.search(text):
        formats.append("graphic_novel")
        audit.append("format_detected:graphic_novel")

    if _ILLUSTRATED_BOOK_RE.search(text):
        formats.append("illustrated_book")
        audit.append("format_detected:illustrated_book")

    # Audience — infer children from STRONG cues unconditionally, or from WEAK
    # cues only when no explicit adult context contradicts them.
    if _STRONG_CHILD_RE.search(text):
        audience = "children"
        audit.append("audience_inferred:children:strong_cue")
    elif _WEAK_CHILD_RE.search(text):
        if _ADULT_CONTEXT_RE.search(text):
            audit.append("audience:not_inferred:weak_cue_suppressed_by_adult_context")
        else:
            audience = "children"
            audit.append("audience_inferred:children:weak_cue")
    else:
        audit.append("audience:not_inferred")

    return BookFormatResult(book_formats=formats, audience=audience, audit=audit)
