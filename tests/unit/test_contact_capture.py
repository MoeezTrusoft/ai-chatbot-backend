from bookcraft.components.leads.contact import ContactCaptureDetector


def test_extracts_name_and_email() -> None:
    # Email alone is no longer sufficient — phone is required for lead_contact_ready.
    r = ContactCaptureDetector().extract("My name is Sarah Khan and my email is sarah@example.com")
    assert r.has_name is True
    assert r.has_email is True
    assert r.lead_contact_ready is False  # phone required
    assert "phone" in r.missing_contact_fields


def test_extracts_name_and_phone() -> None:
    r = ContactCaptureDetector().extract("I am Sarah Khan. Call me at +1 555 123 4567")
    assert r.has_name is True
    assert r.has_phone is True
    assert r.lead_contact_ready is True


def test_rejects_service_phrase_as_name() -> None:
    r = ContactCaptureDetector().extract("my name is editing and email is a@b.com")
    assert r.has_name is False


def test_email_only_not_lead_ready() -> None:
    r = ContactCaptureDetector().extract("email me at sarah@example.com")
    assert r.has_email is True
    assert r.lead_contact_ready is False


def test_name_only_not_lead_ready() -> None:
    r = ContactCaptureDetector().extract("my name is Sarah")
    assert r.has_name is True
    assert r.lead_contact_ready is False


def test_name_and_email_without_phone_not_ready() -> None:
    # Phone is required; email + name is no longer sufficient.
    r = ContactCaptureDetector().extract("this is Sarah, email sarah@example.com")
    assert r.has_name is True
    assert r.has_email is True
    assert r.lead_contact_ready is False
    assert "phone" in r.missing_contact_fields
    # "email_or_phone" field no longer used — phone is tracked separately.
    assert "email_or_phone" not in r.missing_contact_fields
