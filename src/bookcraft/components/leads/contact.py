"""Contact capture detector for lead intake.

Extracts name, email, and phone from user messages.
Lead is considered contact-ready when name + (email OR phone) is present.
Never requires both email and phone.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.components.leads.contact_utils import is_real_contact_value

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ContactInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    source: str | None = None
    confidence: float = 1.0


class ContactCaptureResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contact: ContactInfo
    has_name: bool
    has_email: bool
    has_phone: bool
    lead_contact_ready: bool
    contact_complete: bool = False  # name + email + phone all present
    missing_contact_fields: list[str] = Field(default_factory=list)
    audit: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", re.IGNORECASE)

_PHONE_RE = re.compile(
    r"(?:\+?\d[\d\s().-]{8,}\d)",  # at least 10 digits total
)

# Bare 10+ digit number (no formatting) — treated as phone when present in contact context.
_BARE_PHONE_RE = re.compile(r"\b(\d{10,})\b")

# Strong name patterns only — avoid false positives on service/topic phrases.
_NAME_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bmy\s+name\s+is\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})", re.IGNORECASE),
    re.compile(r"\bi(?:'m|[\s]+am)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})", re.IGNORECASE),
    re.compile(r"\bthis\s+is\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})", re.IGNORECASE),
    re.compile(r"\bname\s*[:=]\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})", re.IGNORECASE),
    re.compile(r"\bcall\s+me\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})", re.IGNORECASE),
]

# Phrases that should never be treated as a person's name.
_FAKE_NAME_TERMS: frozenset[str] = frozenset(
    {
        "editing",
        "ghostwriting",
        "cover design",
        "illustration",
        "bookcraft",
        "manuscript",
        "project",
        "quote",
        "consultation",
        "publishing",
        "formatting",
        "marketing",
        "audiobook",
        "website",
        "trailer",
        "proofreading",
        "unsure",
        "unclear",
    }
)


def _is_fake_name(name: str) -> bool:
    lower = name.strip().lower()
    return lower in _FAKE_NAME_TERMS or any(term in lower for term in _FAKE_NAME_TERMS)


# ---------------------------------------------------------------------------
# Bare-block contact name extractor
# ---------------------------------------------------------------------------


def _extract_bare_contact_name(
    text: str,
    *,
    email: str | None,
    phone: str | None,
) -> str | None:
    """Extract a name from a bare contact block when no structured phrase is found.

    Handles messages like:
        "John Smith john@example.com 5551234567"
        "Sarah Johnson sarah@example.com"
        "Mike Lee +1 555 234 5678"

    Only activates when an email or phone is present in the same message.
    Returns None when the prefix looks like a sentence rather than a name.
    """
    if not email and not phone:
        return None

    # Find the earliest position of email or phone in the text.
    first_marker_index = len(text)
    for marker in [email, phone]:
        if marker and marker in text:
            first_marker_index = min(first_marker_index, text.index(marker))

    prefix = text[:first_marker_index].strip(" ,:-|\t")
    if not prefix:
        return None

    # Extract word-like tokens (letters, hyphens, apostrophes, dots for initials).
    words = re.findall(r"[A-Za-z][A-Za-z.'\-]*", prefix)

    # Too many words → looks like a sentence, not a name.
    if not (1 <= len(words) <= 5):
        return None

    # Must start with a capital letter (proper name convention).
    if not words[0][0].isupper():
        return None

    candidate = " ".join(words)

    # Reject if it matches known fake/service names.
    if _is_fake_name(candidate):
        return None

    # Reject very short single tokens that look like initials or abbreviations
    # without a second word to confirm it's a real name.
    if len(words) == 1 and len(words[0]) <= 2:
        return None

    return candidate


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class ContactCaptureDetector:
    """Extracts contact details from user messages."""

    def extract(self, text: str) -> ContactCaptureResult:
        audit: list[str] = []

        # Email.
        email_match = _EMAIL_RE.search(text)
        email = email_match.group(0).lower() if email_match else None
        if email:
            audit.append(f"email_found:{email}")

        # Phone — formatted or bare 10+ digit number.
        phone_match = _PHONE_RE.search(text)
        phone_raw = phone_match.group(0).strip() if phone_match else None
        # Require at least 10 digits for a valid phone number.
        phone = phone_raw if phone_raw and sum(c.isdigit() for c in phone_raw) >= 10 else None
        # Fallback: a bare run of 10+ digits is a phone number (e.g. "8889050868").
        if phone is None:
            bare_match = _BARE_PHONE_RE.search(text)
            if bare_match and len(bare_match.group(1)) >= 10:
                phone = bare_match.group(1)
                audit.append("phone_bare_digits")
        if phone:
            audit.append(f"phone_found:{phone[:8]}...")

        # Name — structured patterns first.
        name: str | None = None
        for pattern in _NAME_PATTERNS:
            m = pattern.search(text)
            if m:
                candidate = m.group(1).strip()
                if not _is_fake_name(candidate):
                    name = candidate
                    audit.append(f"name_found:{name}")
                    break
                else:
                    audit.append(f"name_rejected_fake:{candidate}")

        # Bare-block fallback: "John Smith john@example.com 5551234567"
        # Only attempt when structured patterns found no name but email/phone exists.
        if name is None and (email or phone):
            bare = _extract_bare_contact_name(text, email=email, phone=phone)
            if bare:
                name = bare
                audit.append(f"name_bare_block:{name}")

        has_name = name is not None
        has_email = email is not None
        has_phone = phone is not None

        # Lead contact ready: name + (email OR phone).
        lead_contact_ready = has_name and (has_email or has_phone)
        # Contact complete: name + email + phone (used for second-method enrichment).
        contact_complete = has_name and has_email and has_phone

        missing: list[str] = []
        if not has_name:
            missing.append("name")
        if not has_email and not has_phone:
            missing.append("email_or_phone")
        elif has_email and not has_phone:
            missing.append("phone")
        elif has_phone and not has_email:
            missing.append("email")

        audit.append(f"lead_contact_ready:{lead_contact_ready}")
        audit.append(f"contact_complete:{contact_complete}")

        return ContactCaptureResult(
            contact=ContactInfo(name=name, email=email, phone=phone, source="chat"),
            has_name=has_name,
            has_email=has_email,
            has_phone=has_phone,
            lead_contact_ready=lead_contact_ready,
            contact_complete=contact_complete,
            missing_contact_fields=missing,
            audit=audit,
        )

    def merge_with_state(self, result: ContactCaptureResult, state: Any) -> ContactCaptureResult:
        """Merge extracted contact with persisted state contact_info.

        Sentinel/redacted values from the state sanitizer are ignored so that
        "[REDACTED_EMAIL]" etc. never look like real contact data.
        """
        existing: dict[str, Any] = getattr(state, "contact_info", {}) or {}

        # Only accept state values that are genuine user-provided strings.
        def _real(v: object) -> object:
            return v if is_real_contact_value(v) else None

        existing_name = _real(existing.get("name"))
        existing_email = _real(existing.get("email"))
        existing_phone = _real(existing.get("phone"))

        name = result.contact.name or existing_name
        email = result.contact.email or existing_email
        phone = result.contact.phone or existing_phone

        has_name = name is not None
        has_email = email is not None
        has_phone = phone is not None
        lead_contact_ready = has_name and (has_email or has_phone)
        contact_complete = has_name and has_email and has_phone

        missing: list[str] = []
        if not has_name:
            missing.append("name")
        if not has_email and not has_phone:
            missing.append("email_or_phone")
        elif has_email and not has_phone:
            missing.append("phone")
        elif has_phone and not has_email:
            missing.append("email")

        return ContactCaptureResult(
            contact=ContactInfo(name=name, email=email, phone=phone, source="chat_merged"),
            has_name=has_name,
            has_email=has_email,
            has_phone=has_phone,
            lead_contact_ready=lead_contact_ready,
            contact_complete=contact_complete,
            missing_contact_fields=missing,
            audit=result.audit + ["merged_with_state"],
        )
