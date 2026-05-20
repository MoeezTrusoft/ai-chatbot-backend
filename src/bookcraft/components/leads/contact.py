"""Contact capture detector for lead intake.

Extracts name, email, and phone from user messages.
Lead is considered contact-ready when name + (email OR phone) is present.
Never requires both email and phone.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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
    missing_contact_fields: list[str] = Field(default_factory=list)
    audit: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", re.IGNORECASE)

_PHONE_RE = re.compile(
    r"(?:\+?\d[\d\s().-]{7,}\d)",
)

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

        # Phone.
        phone_match = _PHONE_RE.search(text)
        phone_raw = phone_match.group(0).strip() if phone_match else None
        # Basic sanity: must have at least 7 digits.
        phone = phone_raw if phone_raw and sum(c.isdigit() for c in phone_raw) >= 7 else None
        if phone:
            audit.append(f"phone_found:{phone[:8]}...")

        # Name.
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

        has_name = name is not None
        has_email = email is not None
        has_phone = phone is not None

        # Lead contact ready: name + (email OR phone).
        lead_contact_ready = has_name and (has_email or has_phone)

        missing: list[str] = []
        if not has_name:
            missing.append("name")
        if not has_email and not has_phone:
            missing.append("email_or_phone")
        elif has_name and not has_email and not has_phone:
            missing.append("email_or_phone")

        audit.append(f"lead_contact_ready:{lead_contact_ready}")

        return ContactCaptureResult(
            contact=ContactInfo(name=name, email=email, phone=phone, source="chat"),
            has_name=has_name,
            has_email=has_email,
            has_phone=has_phone,
            lead_contact_ready=lead_contact_ready,
            missing_contact_fields=missing,
            audit=audit,
        )

    def merge_with_state(self, result: ContactCaptureResult, state: Any) -> ContactCaptureResult:
        """Merge extracted contact with persisted state contact_info."""
        existing: dict[str, Any] = getattr(state, "contact_info", {}) or {}

        name = result.contact.name or existing.get("name")
        email = result.contact.email or existing.get("email")
        phone = result.contact.phone or existing.get("phone")

        has_name = name is not None
        has_email = email is not None
        has_phone = phone is not None
        lead_contact_ready = has_name and (has_email or has_phone)

        missing: list[str] = []
        if not has_name:
            missing.append("name")
        if not (has_email or has_phone):
            missing.append("email_or_phone")

        return ContactCaptureResult(
            contact=ContactInfo(name=name, email=email, phone=phone, source="chat_merged"),
            has_name=has_name,
            has_email=has_email,
            has_phone=has_phone,
            lead_contact_ready=lead_contact_ready,
            missing_contact_fields=missing,
            audit=result.audit + ["merged_with_state"],
        )
