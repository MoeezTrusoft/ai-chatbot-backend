"""ServiceMetadataExtractor — deterministic extraction of service-specific metadata.

Extracts publishing platforms, book formats, ISBN status, and per-service
metadata from user messages. Stores confirmed vs. uncertain/negated candidates
separately. Never overwrites confirmed values with weak candidates.

Engines compute. Claude writes.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Platform extraction patterns
# ---------------------------------------------------------------------------

_PLATFORM_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:amazon\s+kdp|kdp|kindle\s+direct\s+publishing)\b", re.I), "amazon_kdp"),
    (re.compile(r"\b(?:ingramspark|ingram\s+spark|ingram)\b", re.I), "ingramspark"),
    (re.compile(r"\b(?:barnes\s+(?:and|&)\s+noble|b\s*&\s*n|bn\.com)\b", re.I), "barnes_and_noble"),
    (re.compile(r"\bkobo\b", re.I), "kobo"),
    (re.compile(r"\b(?:apple\s+books|ibooks|apple\s+book\s+store)\b", re.I), "apple_books"),
    (re.compile(r"\b(?:google\s+play\s+books?|google\s+play)\b", re.I), "google_play_books"),
    (re.compile(r"\b(?:draft2digital|d2d)\b", re.I), "draft2digital"),
    (
        re.compile(r"\b(?:my\s+website|direct\s+site|shopify|woocommerce|direct\s+sales?)\b", re.I),
        "direct_website",
    ),
    (re.compile(r"\b(?:audible|acx)\b", re.I), "audible_acx"),
]

# ---------------------------------------------------------------------------
# Book format extraction patterns
# ---------------------------------------------------------------------------

_FORMAT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\b(?:e(?:\-)?book|kindle\s+format|kindle\s+edition|digital\s+(?:book|edition))\b",
            re.I,
        ),
        "ebook",
    ),
    (
        re.compile(
            r"\b(?:paperback|print\s+book|soft(?:cover|back)|trade\s+paper(?:back)?)\b", re.I
        ),
        "paperback",
    ),
    (re.compile(r"\b(?:hardcover|hardback|hardbound|case\s+bound)\b", re.I), "hardcover"),
    (
        re.compile(r"\b(?:audiobook|audio\s+book|audio\s+version|audio\s+edition)\b", re.I),
        "audiobook",
    ),
    (re.compile(r"\blarge\s+print\b", re.I), "large_print"),
    (re.compile(r"\bworkbook\b", re.I), "workbook"),
    (re.compile(r"\bpicture\s+book\b", re.I), "picture_book"),
    (re.compile(r"\b(?:comic|graphic\s+novel)\b", re.I), "comic"),
    (re.compile(r"\bcookbook\b", re.I), "cookbook"),
    (re.compile(r"\b(?:journal|planner)\b", re.I), "journal"),
    (re.compile(r"\bbox\s+set\b", re.I), "box_set"),
    (re.compile(r"\bseries\b", re.I), "series"),
]

# ---------------------------------------------------------------------------
# ISBN status patterns
# ---------------------------------------------------------------------------

_ISBN_HAS_RE = re.compile(
    r"\b(?:i\s+(?:already\s+)?have\s+(?:an?\s+)?isbn|already\s+have\s+(?:an?\s+)?isbn|"
    r"isbn\s+(?:is|are|ready|set|done)|have\s+my\s+isbn)\b",
    re.I,
)
_ISBN_NEEDS_RE = re.compile(
    r"\b(?:i\s+need\s+(?:an?\s+)?isbn|need\s+isbn\s+help|help\s+with\s+(?:my\s+)?isbn|"
    r"(?:get|obtain|apply\s+for)\s+(?:an?\s+)?isbn|don'?t\s+have\s+(?:an?\s+)?isbn)\b",
    re.I,
)

# ---------------------------------------------------------------------------
# Negation / uncertainty cue detection
# ---------------------------------------------------------------------------

_NEGATION_WINDOW = 48  # characters to look back for negation

_NEGATION_PREFIX_RE = re.compile(
    r"\b(?:not?|no|don'?t\s*(?:want|need|like)?|do\s+not|without|except|skip|avoid|"
    r"only\s+(?:not?|no)|instead\s+of)\b",
    re.I,
)

_UNCERTAINTY_RE = re.compile(
    r"\b(?:maybe|perhaps|possibly|might|could\s+be|not\s+sure|unsure|"
    r"thinking\s+about|considering|either|or\b|between|undecided)\b",
    re.I,
)

# ---------------------------------------------------------------------------
# Service-specific extraction patterns
# ---------------------------------------------------------------------------

# Editing level
_EDIT_LEVEL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bdevelopmental\s+edit(?:ing|or)?\b", re.I), "developmental_editing"),
    (re.compile(r"\bline\s+edit(?:ing|or)?\b", re.I), "line_editing"),
    (re.compile(r"\bcopy\s*edit(?:ing|or)?\b", re.I), "copyediting"),
    (re.compile(r"\bproofread(?:ing|er)?\b", re.I), "proofreading"),
]

# Dialect
_DIALECT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:us|american|united\s+states)\s+english\b", re.I), "us_english"),
    (re.compile(r"\b(?:uk|british|british\s+english|british\s+style)\b", re.I), "uk_english"),
    (re.compile(r"\bcanadian\s+english\b", re.I), "canadian_english"),
    (re.compile(r"\baustralian\s+english\b", re.I), "australian_english"),
]

# Cover spine
_COVER_SPINE_RE = re.compile(
    r"\b(?:front[,\s]+back[,\s]+and\s+spine|full\s+wrap|front\s+and\s+back|"
    r"back\s+(?:cover|matter)|spine\s+(?:text|design)|needs?\s+(?:a\s+)?spine)\b",
    re.I,
)

# Cover style
_COVER_STYLE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bminimalist(?:\s+(?:cover|design|style))?\b", re.I), "minimalist"),
    (re.compile(r"\billustrated?\s+(?:cover|style)?\b", re.I), "illustrated"),
    (re.compile(r"\bphotograph(?:ic)?\s+(?:cover|style)?\b", re.I), "photographic"),
    (re.compile(r"\bluxury\s+(?:cover|design|style)?\b", re.I), "luxury"),
    (re.compile(r"\b(?:bold\s+)?typograph(?:ic|y)\s+(?:cover|style)?\b", re.I), "bold_typographic"),
    (re.compile(r"\bcinematic\s+(?:cover|style)?\b", re.I), "cinematic"),
]

# Interior formatting
_FORMAT_TABLES_RE = re.compile(r"\b(?:tables?|footnotes?|endnotes?)\b", re.I)
_FORMAT_EBOOK_RE = re.compile(r"\b(?:ebook|e-book|kindle)\s+(?:format(?:ting)?|layout)\b", re.I)
_FORMAT_PRINT_RE = re.compile(r"\bprint\s+(?:format(?:ting)?|layout|ready)\b", re.I)

# Marketing channels
_CHANNEL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bamazon\s+ads?\b", re.I), "amazon_ads"),
    (re.compile(r"\b(?:meta|facebook|fb)\s+ads?\b", re.I), "meta_ads"),
    (re.compile(r"\btiktok\b", re.I), "tiktok"),
    (re.compile(r"\binstagram\b", re.I), "instagram"),
    (re.compile(r"\blinkedin\b", re.I), "linkedin"),
    (re.compile(r"\bemail\s+(?:marketing|campaign|list)\b", re.I), "email"),
    (re.compile(r"\bpr\b|\bpress\s+release\b", re.I), "pr"),
    (re.compile(r"\binfluencer\b", re.I), "influencer"),
]

# Audiobook
_AUDIO_FINISHED_RE = re.compile(
    r"\b(?:already\s+recorded|finished\s+(?:recording|audio)|have\s+(?:the\s+)?audio|"
    r"recording\s+(?:is\s+)?(?:done|ready|complete|finished))\b",
    re.I,
)

# Website
_BOOKING_FORM_RE = re.compile(r"\bbooking\s+(?:form|page|system)\b", re.I)

# Video duration
_VIDEO_DURATION_RE = re.compile(r"\b(\d+)\s*(?:-|\s+)?(?:second|sec)(?:s|ond)?\b", re.I)

# Video platform
_VIDEO_PLATFORM_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\binstagram\b", re.I), "instagram"),
    (re.compile(r"\btiktok\b", re.I), "tiktok"),
    (re.compile(r"\byoutube\b", re.I), "youtube"),
    (re.compile(r"\b(?:my\s+)?website\b", re.I), "website"),
    (re.compile(r"\bamazon\s+author\s+page\b", re.I), "amazon_author_page"),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_CLAUSE_BOUNDARY_RE = re.compile(
    r"[,;]|\b(?:but|however|instead|only|just|rather)\b",
    re.I,
)


def _is_negated(text: str, match_start: int) -> bool:
    """Check if a match is preceded by a negation cue within the look-back window.

    Negation does not propagate across clause boundaries (commas, 'but', 'only') so
    "don't want Amazon, only IngramSpark" correctly negates only Amazon.
    """
    prefix = text[max(0, match_start - _NEGATION_WINDOW) : match_start]
    neg_match = _NEGATION_PREFIX_RE.search(prefix)
    if not neg_match:
        return False
    # If a clause boundary follows the negation cue, negation does not reach this match.
    tail = prefix[neg_match.end() :]
    return not bool(_CLAUSE_BOUNDARY_RE.search(tail))


def _is_uncertain(text: str, match_start: int, match_end: int) -> bool:
    """Check if an uncertainty cue appears near the match."""
    window = text[max(0, match_start - _NEGATION_WINDOW) : match_end + _NEGATION_WINDOW]
    return bool(_UNCERTAINTY_RE.search(window))


def _candidate_entry(
    service: str,
    key: str,
    value: Any,
    certainty: str,
    confidence: float,
    raw_excerpt: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "service": service,
        "key": key,
        "value": value,
        "certainty": certainty,
        "confidence": confidence,
        "raw_excerpt": raw_excerpt,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


class MetadataExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: dict[str, dict[str, Any]] = Field(default_factory=dict)
    candidates: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    publishing_platforms: list[str] = Field(default_factory=list)
    book_formats: list[str] = Field(default_factory=list)
    target_retailers: list[str] = Field(default_factory=list)
    isbn_status: str | None = None
    distribution_goal: str | None = None
    state_deltas: list[Any] = Field(default_factory=list)
    audit: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class ServiceMetadataExtractor:
    """
    Deterministic extraction of service-specific metadata from user messages.

    Confirmed values: explicit, unambiguous, un-negated statements.
    Candidates: uncertain, possibly negated, or ambiguous mentions.
    Never overwrites confirmed state with weak candidates.
    """

    def extract(
        self,
        text: str,
        *,
        active_service: str | None = None,
        existing_confirmed: dict[str, dict[str, Any]] | None = None,
        existing_candidates: dict[str, list[dict[str, Any]]] | None = None,
    ) -> MetadataExtractionResult:
        audit: list[str] = []
        confirmed: dict[str, dict[str, Any]] = {}
        candidates: dict[str, list[dict[str, Any]]] = {}

        confirmed_platforms: list[str] = []
        negated_platforms: list[str] = []
        confirmed_formats: list[str] = []
        negated_formats: list[str] = []

        # ── Publishing platforms ──────────────────────────────────────────
        for pattern, platform_key in _PLATFORM_PATTERNS:
            for m in pattern.finditer(text):
                if _is_negated(text, m.start()):
                    negated_platforms.append(platform_key)
                    audit.append(f"platform_negated:{platform_key}")
                elif _is_uncertain(text, m.start(), m.end()):
                    _add_candidate(
                        candidates,
                        "publishing_distribution",
                        _candidate_entry(
                            "publishing_distribution",
                            "publishing_platforms",
                            platform_key,
                            "uncertain",
                            0.5,
                            m.group(0),
                            "uncertainty cue near platform mention",
                        ),
                    )
                    audit.append(f"platform_uncertain:{platform_key}")
                else:
                    if platform_key not in confirmed_platforms:
                        confirmed_platforms.append(platform_key)
                    audit.append(f"platform_confirmed:{platform_key}")

        # ── Book formats ──────────────────────────────────────────────────
        for pattern, fmt_key in _FORMAT_PATTERNS:
            for m in pattern.finditer(text):
                if _is_negated(text, m.start()):
                    negated_formats.append(fmt_key)
                    audit.append(f"format_negated:{fmt_key}")
                elif _is_uncertain(text, m.start(), m.end()):
                    _add_candidate(
                        candidates,
                        "publishing_distribution",
                        _candidate_entry(
                            "publishing_distribution",
                            "book_formats",
                            fmt_key,
                            "uncertain",
                            0.5,
                            m.group(0),
                            "uncertainty cue near format mention",
                        ),
                    )
                    audit.append(f"format_uncertain:{fmt_key}")
                else:
                    if fmt_key not in confirmed_formats:
                        confirmed_formats.append(fmt_key)
                    audit.append(f"format_confirmed:{fmt_key}")

        # ── ISBN status ───────────────────────────────────────────────────
        isbn_status: str | None = None
        if _ISBN_HAS_RE.search(text):
            isbn_status = "has_isbn"
            audit.append("isbn:has_isbn")
        elif _ISBN_NEEDS_RE.search(text):
            isbn_status = "needs_isbn"
            audit.append("isbn:needs_isbn")

        # ── Service-specific metadata ─────────────────────────────────────
        svc = active_service or ""

        # Editing level + dialect (editing_proofreading)
        if svc in {"editing_proofreading", ""}:
            for pat, level in _EDIT_LEVEL_PATTERNS:
                _m = pat.search(text)
                if _m and not _is_negated(text, _m.start()):
                    _set_confirmed(confirmed, "editing_proofreading", "editing_level", level)
                    audit.append(f"editing_level:{level}")
            for pat, dialect in _DIALECT_PATTERNS:
                _m = pat.search(text)
                if _m and not _is_negated(text, _m.start()):
                    _set_confirmed(confirmed, "editing_proofreading", "dialect", dialect)
                    audit.append(f"dialect:{dialect}")

        # Cover design
        if svc in {"cover_design_illustration", ""}:
            _spine_m = _COVER_SPINE_RE.search(text)
            if _spine_m and not _is_negated(text, _spine_m.start()):
                _set_confirmed(
                    confirmed, "cover_design_illustration", "front_back_spine_needed", True
                )
                audit.append("cover:front_back_spine")
            for pat, style in _COVER_STYLE_PATTERNS:
                _m = pat.search(text)
                if _m and not _is_negated(text, _m.start()):
                    _set_confirmed(confirmed, "cover_design_illustration", "visual_style", style)
                    audit.append(f"cover_style:{style}")

        # Interior formatting
        if svc in {"interior_formatting", ""}:
            _tables_m = _FORMAT_TABLES_RE.search(text)
            if _tables_m and not _is_negated(text, _tables_m.start()):
                _set_confirmed(confirmed, "interior_formatting", "tables_or_footnotes", True)
                audit.append("formatting:tables_or_footnotes")
            if _FORMAT_EBOOK_RE.search(text):
                _set_confirmed(confirmed, "interior_formatting", "ebook_required", True)
                audit.append("formatting:ebook_required")
            if _FORMAT_PRINT_RE.search(text):
                _set_confirmed(confirmed, "interior_formatting", "print_required", True)
                audit.append("formatting:print_required")

        # Marketing channels
        if svc in {"marketing_promotion", ""}:
            channels: list[str] = []
            for pat, channel in _CHANNEL_PATTERNS:
                _m = pat.search(text)
                if _m and not _is_negated(text, _m.start()) and channel not in channels:
                    channels.append(channel)
                    audit.append(f"marketing_channel:{channel}")
            if channels:
                _set_confirmed(confirmed, "marketing_promotion", "channels", channels)
            # Reviews goal
            if re.search(r"\b(?:reviews?|ratings?)\b", text, re.I):
                if not re.search(r"\bno\s+reviews?\b", text, re.I):
                    _set_confirmed(confirmed, "marketing_promotion", "campaign_goal", "reviews")
                    audit.append("marketing_goal:reviews")

        # Audiobook
        if svc in {"audiobook_production", ""}:
            if _AUDIO_FINISHED_RE.search(text):
                _set_confirmed(confirmed, "audiobook_production", "finished_audio_available", True)
                audit.append("audiobook:finished_audio")

        # Author website
        if svc in {"author_website", ""}:
            if _BOOKING_FORM_RE.search(text):
                _set_confirmed(confirmed, "author_website", "booking_form_needed", True)
                audit.append("website:booking_form_needed")

        # Video trailer
        if svc in {"video_trailer", ""}:
            _dur_m = _VIDEO_DURATION_RE.search(text)
            if _dur_m:
                duration = f"{_dur_m.group(1)} seconds"
                _set_confirmed(confirmed, "video_trailer", "duration", duration)
                audit.append(f"video:duration:{duration}")
            for pat, platform in _VIDEO_PLATFORM_PATTERNS:
                _m = pat.search(text)
                if _m and not _is_negated(text, _m.start()):
                    _set_confirmed(confirmed, "video_trailer", "platform", platform)
                    audit.append(f"video:platform:{platform}")
                    break  # take the first match

        audit.append(
            f"platforms:{confirmed_platforms},formats:{confirmed_formats},isbn:{isbn_status}"
        )

        return MetadataExtractionResult(
            confirmed=confirmed,
            candidates=candidates,
            publishing_platforms=confirmed_platforms,
            book_formats=confirmed_formats,
            target_retailers=[
                p for p in confirmed_platforms if p in {"barnes_and_noble", "kobo", "apple_books"}
            ],
            isbn_status=isbn_status,
            audit=audit,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _set_confirmed(
    confirmed: dict[str, dict[str, Any]],
    service: str,
    key: str,
    value: Any,
) -> None:
    if service not in confirmed:
        confirmed[service] = {}
    # Don't overwrite already-confirmed values with weaker candidates.
    # (Explicit correction via "actually"/"instead" handled at a higher level.)
    confirmed[service][key] = value


def _add_candidate(
    candidates: dict[str, list[dict[str, Any]]],
    service: str,
    entry: dict[str, Any],
) -> None:
    if service not in candidates:
        candidates[service] = []
    candidates[service].append(entry)
