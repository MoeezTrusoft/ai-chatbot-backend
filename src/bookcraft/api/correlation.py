from __future__ import annotations

import re
from uuid import uuid4

SAFE_CORRELATION_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def sanitize_correlation_id(value: str | None) -> str:
    if value is None:
        return str(uuid4())

    stripped = value.strip()
    if SAFE_CORRELATION_ID_RE.fullmatch(stripped):
        return stripped

    return str(uuid4())
