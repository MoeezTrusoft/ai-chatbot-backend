from uuid import UUID

from bookcraft.api.correlation import sanitize_correlation_id


def test_sanitize_correlation_id_accepts_safe_value() -> None:
    assert sanitize_correlation_id("safe-id_123:abc.def") == "safe-id_123:abc.def"


def test_sanitize_correlation_id_generates_uuid_for_missing_value() -> None:
    generated = sanitize_correlation_id(None)

    UUID(generated)


def test_sanitize_correlation_id_rejects_header_injection() -> None:
    generated = sanitize_correlation_id("abc\r\nX-Evil: yes")

    UUID(generated)


def test_sanitize_correlation_id_rejects_too_long_value() -> None:
    generated = sanitize_correlation_id("a" * 129)

    UUID(generated)
