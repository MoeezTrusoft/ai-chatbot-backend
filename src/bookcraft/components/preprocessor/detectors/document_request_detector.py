from __future__ import annotations

import re

from bookcraft.components.preprocessor.detectors.common import (
    match_is_counterfactual,
    match_is_negated,
)
from bookcraft.components.preprocessor.schemas import Span

NDA_RE = re.compile(r"\bnda\b", flags=re.IGNORECASE)
NDA_REQUEST_PATTERNS = tuple(
    re.compile(pattern, flags=re.IGNORECASE)
    for pattern in (
        r"\b(?:need|want|send|prepare|provide|generate|create|draft)\s+(?:me\s+)?(?:an?\s+)?nda\b",
        r"\b(?:need|want|send|prepare|provide|generate|create|draft)\b[^.!?;]{0,80}\bnda\b",
        r"\b(?:please\s+)?(?:prepare|send|provide|generate|create|draft)\s+(?:the\s+)?nda\b",
        r"\bdo you provide\s+(?:an?\s+)?nda\b",
        r"\bnda\s+(?:before sharing|please|needed|required)\b",
    )
)
AGREEMENT_REQUEST_PATTERNS = tuple(
    re.compile(pattern, flags=re.IGNORECASE)
    for pattern in (
        r"\b(?:sign|send|prepare|provide|generate|create|draft)\s+(?:me\s+)?(?:the\s+)?(?:service\s+)?agreement\b",
        r"\b(?:service\s+)?agreement\s+(?:today|please|needed|required|now)\b",
        r"\bready\s+to\s+sign\s+(?:the\s+)?(?:service\s+)?agreement\b",
        r"\b(?:send|prepare|provide|generate|create|draft)\s+(?:me\s+)?(?:the\s+)?contract\b",
    )
)


def has_nda_request(
    text: str,
    negation_spans: list[Span] | None = None,
    counterfactual_spans: list[Span] | None = None,
) -> bool:
    if not NDA_RE.search(text):
        return False
    return _has_document_request(text, NDA_REQUEST_PATTERNS, negation_spans, counterfactual_spans)


def has_agreement_request(
    text: str,
    negation_spans: list[Span] | None = None,
    counterfactual_spans: list[Span] | None = None,
) -> bool:
    return _has_document_request(
        text,
        AGREEMENT_REQUEST_PATTERNS,
        negation_spans,
        counterfactual_spans,
    )


def _has_document_request(
    text: str,
    patterns: tuple[re.Pattern[str], ...],
    negation_spans: list[Span] | None,
    counterfactual_spans: list[Span] | None,
) -> bool:
    for pattern in patterns:
        for match in pattern.finditer(text):
            if match_is_negated(text, match, negation_spans):
                continue
            if match_is_counterfactual(text, match, counterfactual_spans):
                continue
            return True
    return False
