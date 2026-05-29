"""Detects price/timeline commitments in CSR messages.

Commitments are stored in a typed list (csr_commitments) separate from the
narrative summary so the bot can acknowledge them without re-committing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CsrCommitment:
    commitment_type: str   # "price_quoted" | "timeline_promised" | "discount_offered"
    text: str              # verbatim snippet from CSR message


_PRICE_RE = re.compile(
    r"(?:we can do|package|price|cost|fee|quote|charged?|it(?:'s| is))\s.*?"
    r"\$[\d,]+(?:\.\d+)?|\$[\d,]+(?:\.\d+)?\s+(?:per|for|including|total)",
    re.IGNORECASE,
)

_TIMELINE_RE = re.compile(
    r"(?:turnaround|ready|deliver(?:ed?y)?|finish(?:ed)?|complete[d]?|have it)\s.*?"
    r"\d+\s*(?:day|week|month)s?|\d+\s*(?:day|week|month)s?\s+turnaround",
    re.IGNORECASE,
)

_DISCOUNT_RE = re.compile(
    r"(?:waive|discount|off|save|complimentary|free\s+(?:of\s+charge|consultation|revision))",
    re.IGNORECASE,
)


def detect_commitments(csr_text: str) -> list[CsrCommitment]:
    """Return a list of typed commitments found in the CSR message."""
    commitments: list[CsrCommitment] = []

    for m in _PRICE_RE.finditer(csr_text):
        commitments.append(CsrCommitment(commitment_type="price_quoted", text=m.group(0).strip()))

    for m in _TIMELINE_RE.finditer(csr_text):
        commitments.append(
            CsrCommitment(commitment_type="timeline_promised", text=m.group(0).strip())
        )

    for m in _DISCOUNT_RE.finditer(csr_text):
        commitments.append(
            CsrCommitment(commitment_type="discount_offered", text=m.group(0).strip())
        )

    return commitments
