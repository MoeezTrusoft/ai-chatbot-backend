from __future__ import annotations

import re

from bookcraft.components.preprocessor.detectors.common import (
    has_unblocked_match,
    match_is_negated,
)
from bookcraft.components.preprocessor.schemas import Span

NON_PRICING_QUOTE_PATTERNS = tuple(
    re.compile(pattern, flags=re.IGNORECASE)
    for pattern in (
        r"\b(can't|cannot|can not|don't|do not|won't|will not)\s+quote\s+"
        r"(a\s+)?(fixed|exact|final|specific)\b",
        r"\bquote\s+(a\s+)?(fixed|exact|final|specific)\b",
        r"\b(use|add|include|insert|rewrite|polish|edit)\s+(this\s+)?quote\b",
        r"\b(author|opening|chapter|book|manuscript|testimonial|line|text)\s+quote\b",
        r"\bquote\s+(from|in)\s+(the\s+)?(book|manuscript|chapter|text)\b",
    )
)
PRICING_PATTERNS = tuple(
    re.compile(pattern, flags=re.IGNORECASE)
    for pattern in (
        r"\bhow much\b",
        r"\b(pricing|price|cost|fee|fees|charge|charges|budget|rate|rates)\b",
        r"\b40 percent\b",
        r"\bcut the price\b",
        r"\bprice by\b",
        r"\b(get|give|send|prepare|provide|need|want)\s+(me\s+)?(a\s+)?"
        r"(price\s+|pricing\s+|cost\s+)?quote\b",
        r"\b(price|pricing|cost)\s+quote\b",
        r"\bquote\s+(me|for|on)\b",
    )
)


def has_pricing_intent(
    text: str,
    negation_spans: list[Span] | None = None,
    counterfactual_spans: list[Span] | None = None,
) -> bool:
    # Pricing questions remain commercial intent even when phrased as
    # hypotheticals ("would you cut the price?"). Negation still blocks them.
    _ = counterfactual_spans
    if _is_non_pricing_quote_usage(text):
        return False

    for pattern in PRICING_PATTERNS:
        for match in pattern.finditer(text):
            if match_is_negated(text, match, negation_spans):
                continue
            return True
    return False


def _is_non_pricing_quote_usage(text: str) -> bool:
    return has_unblocked_match(text, NON_PRICING_QUOTE_PATTERNS)
