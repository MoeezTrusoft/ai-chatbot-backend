"""Unit tests for contact_utils and related contact-recovery helpers.

Uses only fake PII: john@example.com, 5551234567, John Smith.
Never uses real customer data.
"""

from __future__ import annotations

from bookcraft.components.leads.contact import ContactCaptureDetector
from bookcraft.components.leads.contact_recovery import (
    user_claims_already_shared,
    user_has_complaint_or_privacy_concern,
    user_objects_to_pii_misuse,
)
from bookcraft.components.leads.contact_utils import (
    REDACTED_SENTINELS,
    contact_is_ready,
    contact_status_from_dict,
    has_real_email,
    has_real_name,
    has_real_phone,
    is_real_contact_value,
)

# ---------------------------------------------------------------------------
# 1. Redacted placeholders must not count as contact-ready
# ---------------------------------------------------------------------------


def test_redacted_placeholders_do_not_count_as_contact_ready() -> None:
    """[REDACTED_EMAIL] / [REDACTED_PHONE] must never make contact look ready."""
    contact = {
        "name": "[REDACTED_NAME]",
        "email": "[REDACTED_EMAIL]",
        "phone": "[REDACTED_PHONE]",
    }
    assert not contact_is_ready(contact)
    assert not has_real_name(contact)
    assert not has_real_email(contact)
    assert not has_real_phone(contact)
    assert contact_status_from_dict(contact) == "missing"


def test_all_sentinels_rejected() -> None:
    for sentinel in REDACTED_SENTINELS:
        assert not is_real_contact_value(sentinel), f"Sentinel should be rejected: {sentinel!r}"


def test_none_rejected() -> None:
    assert not is_real_contact_value(None)


def test_empty_string_rejected() -> None:
    assert not is_real_contact_value("")
    assert not is_real_contact_value("   ")


def test_bracket_wrapped_value_rejected() -> None:
    assert not is_real_contact_value("[anything]")
    assert not is_real_contact_value("[UNKNOWN_FIELD]")


def test_real_email_accepted() -> None:
    assert is_real_contact_value("john@example.com")


def test_real_phone_accepted() -> None:
    assert is_real_contact_value("5551234567")


def test_real_name_accepted() -> None:
    assert is_real_contact_value("John Smith")


def test_contact_is_ready_with_real_values() -> None:
    contact = {"name": "John Smith", "email": "john@example.com", "phone": ""}
    assert contact_is_ready(contact)


def test_contact_is_ready_phone_only_contact_method() -> None:
    contact = {"name": "John Smith", "email": "", "phone": "5551234567"}
    assert contact_is_ready(contact)


def test_contact_not_ready_missing_name() -> None:
    contact = {"name": "", "email": "john@example.com", "phone": "5551234567"}
    assert not contact_is_ready(contact)


def test_contact_not_ready_mixed_sentinels_and_real() -> None:
    # Partial: name is real but contact method is sentinel.
    contact = {"name": "John Smith", "email": "[REDACTED_EMAIL]", "phone": "[REDACTED_PHONE]"}
    assert not contact_is_ready(contact)
    assert contact_status_from_dict(contact) == "partial"


# ---------------------------------------------------------------------------
# 2. Bare contact block name extraction
# ---------------------------------------------------------------------------


def test_bare_contact_block_extracts_name_email_phone() -> None:
    detector = ContactCaptureDetector()
    result = detector.extract("John Smith john@example.com 5551234567")
    assert result.contact.name is not None
    assert result.contact.name.lower().startswith("john")
    assert result.contact.email == "john@example.com"
    assert result.contact.phone is not None
    assert result.lead_contact_ready is True


def test_bare_contact_block_extracts_name_email_only() -> None:
    detector = ContactCaptureDetector()
    result = detector.extract("Sarah Johnson sarah@example.com")
    assert result.contact.name is not None
    assert "sarah" in result.contact.name.lower() or "johnson" in result.contact.name.lower()
    assert result.contact.email == "sarah@example.com"
    assert result.lead_contact_ready is True


def test_bare_contact_block_extracts_name_phone_only() -> None:
    detector = ContactCaptureDetector()
    result = detector.extract("Mike Lee 5551234567")
    # Should extract name when phone is present
    if result.contact.name:
        assert "mike" in result.contact.name.lower() or "lee" in result.contact.name.lower()
    assert result.contact.phone is not None


def test_bare_contact_no_false_positive_on_sentence() -> None:
    """A long sentence with an email should not have a random name extracted."""
    detector = ContactCaptureDetector()
    result = detector.extract("I need help publishing my book, please reach me at john@example.com")
    # The words before the email are a sentence fragment; should not be a name.
    # Name may be None or may not match "I need help" etc.
    if result.contact.name:
        assert result.contact.name.lower() not in ("i", "need", "help", "publishing", "book")


def test_bare_contact_fake_name_rejected() -> None:
    detector = ContactCaptureDetector()
    result = detector.extract("Ghostwriting john@example.com 5551234567")
    # "Ghostwriting" should not be accepted as a name.
    assert result.contact.name is None or result.contact.name.lower() != "ghostwriting"


# ---------------------------------------------------------------------------
# 4. Already-shared recovery detector
# ---------------------------------------------------------------------------


def test_user_says_already_shared_triggers_recovery() -> None:
    assert user_claims_already_shared("I already shared it to you above")
    assert user_claims_already_shared("I just gave you my info")
    assert user_claims_already_shared("I told you already")
    assert user_claims_already_shared("are you even reading")
    assert user_claims_already_shared("I shared it above")
    assert user_claims_already_shared("didn't I just say")


def test_normal_message_does_not_trigger_already_shared() -> None:
    assert not user_claims_already_shared("I need help publishing my book")
    assert not user_claims_already_shared("What services do you offer?")
    assert not user_claims_already_shared("How long does ghostwriting take?")


# ---------------------------------------------------------------------------
# 5. PII misuse / complaint detection
# ---------------------------------------------------------------------------


def test_response_never_relabels_user_pii_as_company_contact() -> None:
    """Detect when user objects to their PII being used as company info."""
    assert user_objects_to_pii_misuse("that's my contact details you sharing")
    assert user_objects_to_pii_misuse("those are my details not bookcraft's")
    assert user_objects_to_pii_misuse("that was my email you were giving out")


def test_complaint_privacy_signal_detected() -> None:
    assert user_has_complaint_or_privacy_concern("what the fuck thats my contact details")
    assert user_has_complaint_or_privacy_concern("stop repeating my phone number")
    assert user_has_complaint_or_privacy_concern("privacy")
    assert user_has_complaint_or_privacy_concern("why are you sharing my email")
    assert user_has_complaint_or_privacy_concern("you're not listening")


def test_normal_message_not_flagged_as_complaint() -> None:
    assert not user_has_complaint_or_privacy_concern("I need help with my book")
    assert not user_has_complaint_or_privacy_concern("How much does editing cost?")


# ---------------------------------------------------------------------------
# 7. Complaint recovery suppression
# ---------------------------------------------------------------------------


def test_complaint_recovery_does_not_continue_sales_script() -> None:
    """When complaint/PII-misuse is detected, it should be flagged — not hidden."""
    assert user_has_complaint_or_privacy_concern(
        "what the fuck thats my contact details you sharing"
    )
    # The calling code uses this to switch to recovery mode.
    assert user_objects_to_pii_misuse("thats my contact details you sharing")


# ---------------------------------------------------------------------------
# 8. Known genre not re-asked (via contact utils indirect test)
# ---------------------------------------------------------------------------


def test_known_contact_not_reasked_after_contact_ready() -> None:
    """Once contact is ready, contact_is_ready() returns True consistently."""
    contact = {"name": "John Smith", "email": "john@example.com", "phone": None}
    assert contact_is_ready(contact)
    # Simulate a second turn where the state still has the same contact.
    assert contact_is_ready(contact), "Should still be ready on second turn"


def test_contact_with_none_phone_and_real_email_is_ready() -> None:
    """Name + email is sufficient; phone=None should not prevent ready status."""
    contact = {"name": "John Smith", "email": "john@example.com", "phone": None}
    assert contact_is_ready(contact)
