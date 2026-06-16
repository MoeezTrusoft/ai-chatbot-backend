"""Contact value validity helpers.

Provides a single source of truth for deciding whether a contact-info value
is a *real* user-supplied value vs. a redaction sentinel or empty placeholder.

The state sanitizer replaces real PII with sentinel strings before DB
persistence (e.g. "[REDACTED_EMAIL]").  When state is reloaded the sentinel
is a non-empty string, so naive ``bool(contact_info.get("email"))`` returns
True — making the system believe a real contact method is available when it
is not.  These helpers fix that gap everywhere.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Name / phone validity guards (context-free, shared by the deterministic
# ContactCaptureDetector and the LLM metadata extractor).
# ---------------------------------------------------------------------------

# Timezone abbreviations that must never be captured as a person's name. A reply
# like "EST - clifford@example.com" (answering "what timezone are you in?") put
# "EST" into personal.name before this guard existed.
_TIMEZONE_ABBREVS: frozenset[str] = frozenset(
    {
        "est", "edt", "cst", "cdt", "mst", "mdt", "pst", "pdt", "akst", "akdt",
        "hst", "hast", "hadt", "gmt", "utc", "bst", "ist", "pkt", "cet", "cest",
        "eet", "eest", "aest", "aedt", "acst", "awst", "nzst", "nzdt", "jst",
        "kst", "sgt", "wib", "wita", "msk", "et", "ct", "mt", "pt",
    }
)

# Filler / non-name words that frequently follow "I am" / "I'm" / "this is" and
# must not be captured as a name (the patterns are case-insensitive, so a phrase
# like "I am looking for a ghostwriter" otherwise yields the name "looking for").
_NON_NAME_WORDS: frozenset[str] = frozenset(
    {
        # Verbs / fillers that follow "I am" / "I'm".
        "looking", "working", "trying", "interested", "writing", "hoping", "ready",
        "good", "fine", "okay", "here", "not", "sure", "still", "just", "going",
        "thinking", "wondering", "currently", "also", "done", "happy", "glad",
        "excited", "unsure", "unclear", "planning", "searching", "seeking", "needing",
        "wanting", "considering", "exploring", "new", "really", "very", "based",
        "located", "available", "free", "busy", "open", "starting",
        # Leading prepositions / articles ("I am in central time" → "in central time").
        "in", "at", "on", "of", "to", "for", "with", "from", "into", "about",
        "an", "the", "a", "my", "we", "our", "us", "your",
    }
)

# A year/era range such as "1770-1810" — a historical period, never a phone number.
_YEAR_RANGE_RE = re.compile(r"\b(1[5-9]\d{2}|20\d{2})\s*(?:[-–—]|to)\s*(1[5-9]\d{2}|20\d{2})\b")


def is_timezone_token(value: object) -> bool:
    """True when *value* is a bare timezone abbreviation (e.g. "EST", "pst.")."""
    if not isinstance(value, str):
        return False
    return value.strip().strip(".").lower() in _TIMEZONE_ABBREVS


def is_non_name_token(value: object) -> bool:
    """True when a candidate *name* is actually a timezone, filler word, or abbreviation.

    Used to reject false-positive names from both the deterministic bare-block
    extractor and the LLM extractor before they reach ``personal.name``.
    """
    if not isinstance(value, str):
        return True
    v = value.strip()
    if not v:
        return True
    if is_timezone_token(v):
        return True
    tokens = v.split()
    if tokens and tokens[0].lower() in _NON_NAME_WORDS:
        return True
    # A single all-caps short token is almost always an abbreviation (EST, CEO, USA),
    # not a name a person typed for themselves.
    if len(tokens) == 1 and v.isupper() and len(v) <= 4:
        return True
    return False


def looks_like_year_or_date_range(text: object) -> bool:
    """True when *text* contains a historical year/era range (e.g. "1770-1810")."""
    if not isinstance(text, str):
        return False
    return bool(_YEAR_RANGE_RE.search(text))


def is_valid_phone(value: object) -> bool:
    """True when *value* is plausibly a real phone number.

    A phone must carry 10–15 digits (NANP through E.164) and must not be a
    year/era range like "1770-1810" or an age range like "6-12".
    """
    if not isinstance(value, str) or not value.strip():
        return False
    if looks_like_year_or_date_range(value):
        return False
    digit_count = sum(c.isdigit() for c in value)
    return 10 <= digit_count <= 15

# All placeholder strings emitted by the state sanitizer / redaction layer.
# Add new ones here if the sanitizer gains new sentinel formats.
REDACTED_SENTINELS: frozenset[str] = frozenset(
    {
        "[REDACTED_NAME]",
        "[REDACTED_EMAIL]",
        "[REDACTED_PHONE]",
        "[REDACTED_NUMBER]",
        "[REDACTED_URL]",
        # Lower-case variants (defensive).
        "[redacted_name]",
        "[redacted_email]",
        "[redacted_phone]",
        "[redacted_number]",
        "[redacted_url]",
        # Generic test/placeholder strings that should never reach production.
        "redacted",
        "[redacted]",
        "none",
        "null",
        "n/a",
        "na",
    }
)


def is_real_contact_value(value: object) -> bool:
    """Return True only when *value* is a genuine user-supplied contact detail.

    Returns False for:
    - None or non-string types
    - Empty or whitespace-only strings
    - Known redaction sentinels (e.g. "[REDACTED_EMAIL]")
    - Obvious placeholder strings
    """
    if value is None:
        return False
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if not stripped:
        return False
    if stripped.lower() in REDACTED_SENTINELS:
        return False
    # Brackets around the whole value are a strong sentinel indicator.
    if stripped.startswith("[") and stripped.endswith("]"):
        return False
    return True


def has_real_name(contact_info: dict[str, Any]) -> bool:
    """Return True when contact_info contains a real (non-sentinel) name."""
    return is_real_contact_value(contact_info.get("name"))


def has_real_email(contact_info: dict[str, Any]) -> bool:
    """Return True when contact_info contains a real (non-sentinel) email."""
    return is_real_contact_value(contact_info.get("email"))


def has_real_phone(contact_info: dict[str, Any]) -> bool:
    """Return True when contact_info contains a real (non-sentinel) phone."""
    return is_real_contact_value(contact_info.get("phone"))


def contact_is_ready(contact_info: dict[str, Any]) -> bool:
    """Return True when name + (email OR phone) are all real values.

    Mirrors the lead-readiness rule: name is required; exactly one of email
    or phone is sufficient.  Sentinel/placeholder values are not counted.
    """
    return has_real_name(contact_info) and (
        has_real_email(contact_info) or has_real_phone(contact_info)
    )


def contact_is_complete(contact_info: dict[str, Any]) -> bool:
    """Return True when name + email + phone are all real values (fully enriched)."""
    return (
        has_real_name(contact_info)
        and has_real_email(contact_info)
        and has_real_phone(contact_info)
    )


def contact_status_from_dict(contact_info: dict[str, Any]) -> str:
    """Return a human-readable contact completeness status.

    Returns one of: "ready", "partial", "missing".
    """
    name_ok = has_real_name(contact_info)
    email_ok = has_real_email(contact_info)
    phone_ok = has_real_phone(contact_info)

    if name_ok and (email_ok or phone_ok):
        return "ready"
    if name_ok or email_ok or phone_ok:
        return "partial"
    return "missing"
