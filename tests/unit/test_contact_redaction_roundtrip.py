"""Contact survives persistence → cross-turn lead assembly works (room 5988 fix).

Previously the state sanitizer redacted personal.name/email/phone before DB
persistence, so contact supplied across turns (email now, name later) never
reconciled: each prior value reloaded as a [REDACTED_*] sentinel, merge_with_state
rejected it, lead_contact_ready never became True, and the lead was never created
or synced to CSR (the AI State panel showed [REDACTED_NAME]).

Policy fix: the structured contact fields are now PRESERVED through persistence
(message excerpts and the rolling summary stay redacted). These tests lock that:
contact survives the round-trip and a lead assembled across turns becomes ready.
"""
from __future__ import annotations

from bookcraft.components.leads.contact import ContactCaptureDetector
from bookcraft.components.leads.contact_utils import is_real_contact_value
from bookcraft.components.storage.state_sanitizer import (
    sanitize_thread_state_for_persistence,
)
from bookcraft.domain.enums import Source
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ThreadState


def _state_with(*, name=None, email=None, phone=None) -> ThreadState:
    state = ThreadState()
    ci: dict[str, str] = {}
    if name is not None:
        state.personal.name = FieldMeta[str](value=name, confidence=0.92, source=Source.USER_STATED)
        ci["name"] = name
    if email is not None:
        state.personal.email = FieldMeta[str](value=email, confidence=0.98, source=Source.USER_STATED)
        ci["email"] = email
    if phone is not None:
        state.personal.phone = FieldMeta[str](value=phone, confidence=0.95, source=Source.USER_STATED)
        ci["phone"] = phone
    try:
        state.contact_info = ci  # type: ignore[attr-defined]
    except Exception:
        pass
    return state


def _roundtrip(state: ThreadState) -> ThreadState:
    return ThreadState.model_validate(sanitize_thread_state_for_persistence(state))


class TestContactSurvivesPersistence:
    def test_personal_contact_preserved(self) -> None:
        snap = sanitize_thread_state_for_persistence(
            _state_with(name="Ann", email="clifford@safarisolutions.com", phone="3174134221")
        )
        assert snap["personal"]["name"]["value"] == "Ann"
        assert snap["personal"]["email"]["value"] == "clifford@safarisolutions.com"
        assert snap["personal"]["phone"]["value"] == "3174134221"

    def test_reloaded_contact_is_real(self) -> None:
        reloaded = _roundtrip(_state_with(email="clifford@safarisolutions.com"))
        assert reloaded.personal.email.value == "clifford@safarisolutions.com"
        assert is_real_contact_value(reloaded.personal.email.value) is True


class TestCrossTurnLeadAssembly:
    def test_email_then_name_phone_becomes_ready(self) -> None:
        d = ContactCaptureDetector()
        # Turn A: email only → persisted (and now preserved on reload).
        reloaded = _roundtrip(_state_with(email="clifford@safarisolutions.com"))
        # Turn B: name + phone.
        turn_b = d.extract("Ann 3174134221")
        merged = d.merge_with_state(turn_b, reloaded)
        assert merged.has_name is True
        assert merged.has_email is True   # survived from turn A
        assert merged.has_phone is True
        assert merged.lead_contact_ready is True
        assert merged.contact.email == "clifford@safarisolutions.com"

    def test_room_5988_name_plus_email_ready(self) -> None:
        # Name + email captured across turns, phone never — now ready (name + email).
        d = ContactCaptureDetector()
        reloaded = _roundtrip(_state_with(name="Ann", email="clifford@safarisolutions.com"))
        merged = d.merge_with_state(d.extract("ok"), reloaded)
        assert merged.has_name is True
        assert merged.has_email is True
        assert merged.lead_contact_ready is True


class TestFreeTextStillRedacted:
    def test_rolling_summary_redacted(self) -> None:
        state = _state_with(email="clifford@safarisolutions.com")
        state.rolling_summary = "reach me at clifford@safarisolutions.com or 555-123-9999"
        snap = sanitize_thread_state_for_persistence(state)
        # Contact field preserved…
        assert snap["personal"]["email"]["value"] == "clifford@safarisolutions.com"
        # …but the free-text summary has the email redacted.
        assert "clifford@safarisolutions.com" not in snap["rolling_summary"]
        assert "[REDACTED_EMAIL]" in snap["rolling_summary"]
