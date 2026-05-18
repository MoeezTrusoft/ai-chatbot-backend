from __future__ import annotations

import re

MONTH_PATTERN = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?"
)

DATE_HINT_RE = re.compile(
    rf"\b(?:"
    rf"\d{{1,2}}[/-]\d{{1,2}}(?:[/-]\d{{2,4}})?|"
    rf"(?:{MONTH_PATTERN})\b(?:\s+\d{{1,2}}(?:,\s*\d{{4}})?)?"
    rf")\b",
    flags=re.IGNORECASE,
)


def has_date_hint(text: str) -> bool:
    return bool(DATE_HINT_RE.search(text))
