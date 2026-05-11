from bookcraft.infra.redaction import redact_mapping, redact_text


def test_redact_text_removes_email_phone_url_and_long_number() -> None:
    text = (
        "Email me at author@example.com or call +1 555-123-4567. "
        "Portfolio: https://example.com/private. Card 4242424242424242."
    )

    redacted = redact_text(text)

    assert "author@example.com" not in redacted
    assert "+1 555-123-4567" not in redacted
    assert "https://example.com/private" not in redacted
    assert "4242424242424242" not in redacted
    assert "[REDACTED_EMAIL]" in redacted
    assert "[REDACTED_PHONE]" in redacted
    assert "[REDACTED_URL]" in redacted
    assert "[REDACTED_NUMBER]" in redacted


def test_redact_mapping_recursively_redacts_nested_values() -> None:
    payload = {
        "message": "My email is author@example.com",
        "nested": {
            "phone": "+92 300 1234567",
            "items": ["https://example.com/private"],
        },
    }

    redacted = redact_mapping(payload)

    assert redacted is not None
    assert "author@example.com" not in str(redacted)
    assert "+92 300 1234567" not in str(redacted)
    assert "https://example.com/private" not in str(redacted)
