from __future__ import annotations

import re
from typing import Any

EMAIL_RE = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    flags=re.IGNORECASE,
)

URL_RE = re.compile(
    r"\bhttps?://[^\s<>()]+",
    flags=re.IGNORECASE,
)

PHONE_RE = re.compile(
    r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)",
)

LONG_DIGIT_RE = re.compile(
    r"(?<!\d)\d{12,19}(?!\d)",
)


def redact_text(text: str) -> str:
    redacted = EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    redacted = URL_RE.sub("[REDACTED_URL]", redacted)
    redacted = LONG_DIGIT_RE.sub("[REDACTED_NUMBER]", redacted)
    redacted = PHONE_RE.sub("[REDACTED_PHONE]", redacted)
    return redacted


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)

    if isinstance(value, list):
        return [redact_value(item) for item in value]

    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)

    if isinstance(value, dict):
        return {str(key): redact_value(item) for key, item in value.items()}

    return value


def redact_mapping(value: dict[str, object] | None) -> dict[str, object] | None:
    if value is None:
        return None
    redacted = redact_value(value)
    if not isinstance(redacted, dict):
        return {}
    return redacted
