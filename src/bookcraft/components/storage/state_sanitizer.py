from __future__ import annotations

from typing import Any

from bookcraft.domain.state import ThreadState
from bookcraft.infra.redaction import redact_mapping, redact_text

# Structured contact fields that are PRESERVED in the persisted thread state so
# the CSR "AI State" panel shows them and cross-turn lead assembly works. (Product
# decision: contact provided across turns must survive reload — otherwise the lead
# is never created. Raw message excerpts and the rolling summary are still redacted.)
_CONTACT_KEYS = ("name", "email", "phone")


def sanitize_thread_state_for_persistence(state: ThreadState) -> dict[str, Any]:
    """Return a DB-safe thread-state snapshot.

    Message excerpts, the rolling summary, and stray PII in free-text fields are
    redacted. The customer's *structured* contact fields (personal.name/email/phone
    and contact_info) are deliberately preserved in plaintext: the CSR AI State panel
    surfaces them, and lead assembly needs them to persist across turns.
    """

    raw = state.model_dump(mode="json")
    redacted = redact_mapping(raw) or {}
    _restore_executable_pending_confirmation_payload(raw, redacted)
    _restore_contact_fields(raw, redacted)
    _sanitize_raw_excerpts(redacted)
    _sanitize_rolling_summary(redacted)
    return redacted


def _restore_contact_fields(raw: dict[str, Any], snapshot: dict[str, Any]) -> None:
    """Restore the real contact values that ``redact_mapping`` masked.

    ``redact_mapping`` redacts email/phone-shaped strings everywhere, including
    ``personal.email``/``personal.phone`` and ``contact_info``. We copy the original
    values back so the structured contact survives persistence. Only the ``value`` is
    restored; each field's ``raw_excerpt`` (the source message snippet) stays redacted
    via ``_sanitize_raw_excerpts``.
    """
    raw_personal = raw.get("personal") if isinstance(raw.get("personal"), dict) else {}
    snap_personal = snapshot.get("personal")
    if isinstance(snap_personal, dict):
        for key in _CONTACT_KEYS:
            raw_fm = raw_personal.get(key)
            snap_fm = snap_personal.get(key)
            if (
                isinstance(raw_fm, dict)
                and isinstance(snap_fm, dict)
                and raw_fm.get("value") is not None
            ):
                snap_fm["value"] = raw_fm["value"]

    raw_ci = raw.get("contact_info") if isinstance(raw.get("contact_info"), dict) else {}
    snap_ci = snapshot.get("contact_info")
    if isinstance(snap_ci, dict):
        for key in _CONTACT_KEYS:
            if raw_ci.get(key) is not None:
                snap_ci[key] = raw_ci[key]


def _sanitize_raw_excerpts(value: Any) -> None:
    if isinstance(value, dict):
        if "raw_excerpt" in value and value["raw_excerpt"] is not None:
            value["raw_excerpt"] = redact_text(str(value["raw_excerpt"]))
        for item in value.values():
            _sanitize_raw_excerpts(item)
        return

    if isinstance(value, list):
        for item in value:
            _sanitize_raw_excerpts(item)


def _sanitize_rolling_summary(snapshot: dict[str, Any]) -> None:
    summary = snapshot.get("rolling_summary")
    if isinstance(summary, str):
        snapshot["rolling_summary"] = redact_text(summary)


def _restore_executable_pending_confirmation_payload(
    raw: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    """Keep short-lived action-confirmation payloads executable.

    Most thread state should be redacted before persistence. Pending action payloads
    are different: they are cleared after confirmation and are needed to execute
    the exact customer-approved action on the next turn. Without this, the bot
    reloads [REDACTED_EMAIL]/[REDACTED_PHONE] and document generation fails.
    """

    raw_pending = (
        raw.get("sales_actions", {}).get("pending_confirmation", {})
        if isinstance(raw.get("sales_actions"), dict)
        else {}
    )
    pending_type = raw_pending.get("type") if isinstance(raw_pending, dict) else None
    raw_payload = raw_pending.get("payload") if isinstance(raw_pending, dict) else None

    if pending_type not in {"generate_nda", "generate_agreement", "schedule_consultation"}:
        return
    if not isinstance(raw_payload, dict):
        return

    sales_actions = snapshot.get("sales_actions")
    if not isinstance(sales_actions, dict):
        return

    pending = sales_actions.get("pending_confirmation")
    if not isinstance(pending, dict):
        return

    pending["payload"] = raw_payload
