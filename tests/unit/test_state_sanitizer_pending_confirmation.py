from __future__ import annotations

from bookcraft.components.storage.state_sanitizer import (
    sanitize_thread_state_for_persistence,
)
from bookcraft.domain.state import ThreadState


def test_sanitizer_preserves_nda_pending_confirmation_payload() -> None:
    state = ThreadState()
    state.sales_actions.pending_confirmation.type = "generate_nda"
    state.sales_actions.pending_confirmation.payload = {
        "name": "Maya Author",
        "email": "maya@example.com",
        "phone": "+1 555 123 4567",
        "effective_date": "2026-05-18",
        "send_email": False,
    }

    snapshot = sanitize_thread_state_for_persistence(state)

    payload = snapshot["sales_actions"]["pending_confirmation"]["payload"]

    assert payload["name"] == "Maya Author"
    assert payload["email"] == "maya@example.com"
    assert payload["phone"] == "+1 555 123 4567"
    assert payload["effective_date"] == "2026-05-18"
    assert payload["send_email"] is False


def test_sanitizer_preserves_structured_contact() -> None:
    # Policy change: structured contact (name/email/phone) is preserved in the
    # persisted state so it shows in the CSR AI State panel and survives across turns.
    state = ThreadState()
    state.personal.email.value = "maya@example.com"
    state.personal.phone.value = "+1 555 123 4567"

    snapshot = sanitize_thread_state_for_persistence(state)

    assert snapshot["personal"]["email"]["value"] == "maya@example.com"
    assert snapshot["personal"]["phone"]["value"] == "+1 555 123 4567"
