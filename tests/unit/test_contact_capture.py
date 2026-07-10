from bookcraft.components.leads.contact import ContactCaptureDetector


def test_extracts_name_and_email() -> None:
    # Email alone (with name) is sufficient for lead_contact_ready.
    r = ContactCaptureDetector().extract("My name is Sarah Khan and my email is sarah@example.com")
    assert r.has_name is True
    assert r.has_email is True
    assert r.lead_contact_ready is True   # name + email = ready
    assert "phone" in r.missing_contact_fields  # still asks for phone as supplementary


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


def test_name_and_email_is_ready() -> None:
    # Name + email is sufficient for lead_contact_ready (phone is preferred but not blocking).
    r = ContactCaptureDetector().extract("this is Sarah, email sarah@example.com")
    assert r.has_name is True
    assert r.has_email is True
    assert r.lead_contact_ready is True   # name + email = ready
    assert "phone" in r.missing_contact_fields   # phone still asked as supplementary
    assert "email_or_phone" not in r.missing_contact_fields


def test_burst_merged_name_does_not_bleed_into_next_line() -> None:
    # chat 6688: rapid messages are newline-joined before extraction, and the name
    # pattern's `\s+` separator matched across the newline, capturing the next
    # line's first word ("Deborah Houston\nHe"). The name must stay clean.
    r = ContactCaptureDetector().extract(
        "My name is Deborah Houston\nHe was in prison for 35 years"
    )
    assert r.contact.name == "Deborah Houston"
    assert "\n" not in (r.contact.name or "")


def test_multi_word_name_across_newline_is_truncated_to_first_line() -> None:
    r = ContactCaptureDetector().extract("this is Sarah Khan\nAnd she wrote a memoir")
    assert r.contact.name == "Sarah Khan"
