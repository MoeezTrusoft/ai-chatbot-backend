"""PII/contact masking before language detection to prevent false non-English flags."""

from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", flags=re.IGNORECASE)
_PHONE_RE = re.compile(r"\+?\d[\d\s().+-]{6,}\d")
# Name: at least 2 consecutive Title-Case words (avoids single-word false positives like "Send").
_NAME_RE = re.compile(r"\b([A-Z][a-zA-ZÀ-ɏ]{1,24})(?:\s+[A-Z][a-zA-ZÀ-ɏ]{1,24}){1,3}\b")

_PII_PLACEHOLDER_RE = re.compile(r"\[(?:EMAIL|PHONE|NAME)\]")
_SEPARATOR_RE = re.compile(r"[,;:\-/|\s]+")


class PIIMaskResult:
    __slots__ = ("masked_text", "has_pii", "pii_types")

    def __init__(self, masked_text: str, has_pii: bool, pii_types: list[str]) -> None:
        self.masked_text = masked_text
        self.has_pii = has_pii
        self.pii_types = pii_types


def mask_pii(text: str) -> PIIMaskResult:
    """
    Mask PII spans (email, phone, name patterns) before language detection.

    Language detection must not reject messages that are mostly contact info.
    Masking order: email first, then phone, then name — to prevent name regex
    from matching email local-parts.
    """
    masked = text
    pii_types: list[str] = []

    # Email must be masked first so the name regex doesn't match email local-parts.
    if _EMAIL_RE.search(masked):
        masked = _EMAIL_RE.sub("[EMAIL]", masked)
        pii_types.append("email")

    if _PHONE_RE.search(masked):
        masked = _PHONE_RE.sub("[PHONE]", masked)
        pii_types.append("phone")

    # Name: at least 2 Title-Case words, only after email/phone are already masked.
    if _NAME_RE.search(masked):
        masked = _NAME_RE.sub("[NAME]", masked)
        pii_types.append("name")

    return PIIMaskResult(
        masked_text=masked,
        has_pii=bool(pii_types),
        pii_types=pii_types,
    )


def is_predominantly_pii(text: str) -> bool:
    """
    Return True when text is predominantly PII/contact information.

    Used to bypass language detection entirely for contact-capture turns.
    Examples: 'Maham Qureshi', 'sarah@example.com', '+92 300 1234567',
    'Sarah, sarah@example.com'
    """
    result = mask_pii(text)
    if not result.has_pii:
        return False

    # Strip placeholders and separators; if very little content remains → predominantly PII.
    remaining = _PII_PLACEHOLDER_RE.sub("", result.masked_text)
    remaining = _SEPARATOR_RE.sub("", remaining).strip()
    return len(remaining) < 15
