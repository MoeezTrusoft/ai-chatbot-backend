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

from typing import Any

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
