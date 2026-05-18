from __future__ import annotations

from typing import Any

from bookcraft.domain.state import ThreadState
from bookcraft.infra.redaction import redact_mapping, redact_text

_REDACTED_NAME = "[REDACTED_NAME]"
_REDACTED_EMAIL = "[REDACTED_EMAIL]"
_REDACTED_PHONE = "[REDACTED_PHONE]"


def sanitize_thread_state_for_persistence(state: ThreadState) -> dict[str, Any]:
    """Return a DB-safe thread-state snapshot.

    Thread state is useful for routing and continuity, but it should not persist
    direct contact PII or raw message excerpts. High-risk contact values are
    replaced with placeholders; all remaining string fields are passed through
    the project redactor for emails, phones, URLs, and long numbers.
    """

    raw = state.model_dump(mode="json")
    redacted = redact_mapping(raw) or {}
    _restore_executable_pending_confirmation_payload(raw, redacted)
    _sanitize_personal_info(redacted)
    _sanitize_raw_excerpts(redacted)
    _sanitize_rolling_summary(redacted)
    return redacted


def _sanitize_personal_info(snapshot: dict[str, Any]) -> None:
    personal = snapshot.get("personal")
    if not isinstance(personal, dict):
        return

    _replace_field_meta_value(personal, "name", _REDACTED_NAME)
    _replace_field_meta_value(personal, "email", _REDACTED_EMAIL)
    _replace_field_meta_value(personal, "phone", _REDACTED_PHONE)


def _replace_field_meta_value(
    container: dict[str, Any],
    key: str,
    replacement: str,
) -> None:
    field_meta = container.get(key)
    if not isinstance(field_meta, dict):
        return
    if field_meta.get("value") is not None:
        field_meta["value"] = replacement
        field_meta["raw_excerpt"] = None


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
