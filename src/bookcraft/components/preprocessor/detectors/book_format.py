"""BookFormatDetector — detects book format/type without assuming genre or audience."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

_PICTURE_BOOK_RE = re.compile(r"\bpicture\s+book\b", flags=re.IGNORECASE)
_BOARD_BOOK_RE = re.compile(r"\bboard\s+book\b", flags=re.IGNORECASE)
_CHAPTER_BOOK_RE = re.compile(r"\bchapter\s+book\b", flags=re.IGNORECASE)
_GRAPHIC_NOVEL_RE = re.compile(r"\bgraphic\s+novel\b", flags=re.IGNORECASE)
_ILLUSTRATED_BOOK_RE = re.compile(r"\billustrated\s+book\b", flags=re.IGNORECASE)

# Only infer children audience when these explicit child-audience words appear.
_CHILDREN_AUDIENCE_RE = re.compile(
    r"\b(?:children|kids?|young\s+readers?|ages?\s+\d|bedtime\s+stor(?:y|ies)|"
    r"school\s+book|toddlers?|preschool|elementary|juvenile|"
    r"for\s+(?:my\s+)?(?:child|kid|daughter|son))\b",
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

    # Audience — only infer when explicit child-audience words appear.
    if _CHILDREN_AUDIENCE_RE.search(text):
        audience = "children"
        audit.append("audience_inferred:children")
    else:
        audit.append("audience:not_inferred")

    return BookFormatResult(book_formats=formats, audience=audience, audit=audit)
