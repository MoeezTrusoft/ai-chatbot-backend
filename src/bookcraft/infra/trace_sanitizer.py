"""Trace-safe serializers for contact/lead data.

Live traces must never contain raw PII (name, email, phone).
This module replaces raw contact fields with safe booleans and masked values.
"""

from __future__ import annotations

import re
from typing import Any


def _mask_email(email: str) -> str:
    """'john@example.com' → 'j***@example.com'."""
    at = email.find("@")
    if at <= 0:
        return "***"
    local = email[:at]
    domain = email[at:]
    if len(local) <= 1:
        return f"{local}***{domain}"
    return f"{local[0]}***{domain}"


def _mask_phone(phone: str) -> str:
    """'5551234567' → '***4567'."""
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 4:
        return "***"
    return f"***{digits[-4:]}"


def safe_contact_capture(result: Any) -> dict[str, Any]:
    """Return a trace-safe dict from a ContactCaptureResult.

    Replaces raw name/email/phone with booleans and masked values.
    Preserves all routing/readiness fields.
    """
    if result is None:
        return {}

    raw = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
    contact = raw.get("contact") or {}

    email_val = contact.get("email") or ""
    phone_val = contact.get("phone") or ""

    safe: dict[str, Any] = {
        "has_name": raw.get("has_name", False),
        "has_email": raw.get("has_email", False),
        "has_phone": raw.get("has_phone", False),
        "lead_contact_ready": raw.get("lead_contact_ready", False),
        "missing_contact_fields": raw.get("missing_contact_fields", []),
        "audit": raw.get("audit", []),
    }

    if email_val:
        safe["email_masked"] = _mask_email(email_val)
    if phone_val:
        safe["phone_masked"] = _mask_phone(phone_val)

    return safe


def safe_lead_intake(payload: Any) -> dict[str, Any]:
    """Return a trace-safe version of lead_intake_payload.

    Replaces known PII fields with booleans/masked values.
    """
    if not payload:
        return {}

    raw: dict[str, Any] = dict(payload) if isinstance(payload, dict) else {}

    safe: dict[str, Any] = {
        "has_name": bool(raw.get("name")),
        "has_email": bool(raw.get("email")),
        "has_phone": bool(raw.get("phone")),
        "has_service": bool(raw.get("services") or raw.get("service")),
        "has_thread_id": bool(raw.get("thread_id")),
    }

    email = raw.get("email") or ""
    phone = raw.get("phone") or ""
    if email:
        safe["email_masked"] = _mask_email(str(email))
    if phone:
        safe["phone_masked"] = _mask_phone(str(phone))

    # Preserve non-PII fields useful for debugging.
    for key in ("services", "service", "thread_id", "source", "lead_stage"):
        if key in raw:
            safe[key] = raw[key]

    return safe


def sanitize_event_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Sanitize event payloads before persistence.

    For user.message events: replace raw text with metadata.
    For other events: apply redaction and scrub contact fields.
    """
    from bookcraft.infra.redaction import EMAIL_RE, PHONE_RE

    if event_type == "user.message":
        text = payload.get("text") or ""
        has_email = bool(EMAIL_RE.search(text))
        has_phone = bool(PHONE_RE.search(text))
        safe: dict[str, Any] = {
            "message_length": len(text),
            "pii_redacted": has_email or has_phone,
            "has_email": has_email,
            "has_phone": has_phone,
        }
        if has_email or has_phone:
            # Keep a redacted preview for debugging but not the raw text.
            from bookcraft.infra.redaction import redact_text

            safe["text_redacted"] = redact_text(text)[:300]
        else:
            # Safe to keep short messages that contain no PII.
            safe["text"] = text[:300] if len(text) <= 300 else text[:300] + "…"
        return safe

    # For all other events: apply standard redaction.
    from bookcraft.infra.redaction import redact_mapping

    return redact_mapping(payload) or {}
