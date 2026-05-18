from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from re import Match, Pattern

from bookcraft.components.preprocessor.schemas import Span

NEGATION_PREFIX_RE = re.compile(
    r"(?:\b(?:no|not|never|without|skip|avoid|decline|reject|"
    r"don't|dont|do not|doesn't|does not|didn't|did not|"
    r"can't|cannot|can not|won't|will not|haven't|have not|hasn't|has not|"
    r"isn't|is not|aren't|are not|wasn't|was not|weren't|were not)\b)\W*$",
    flags=re.IGNORECASE,
)
NEGATION_CUE_RE = re.compile(
    r"\b(?:no|not|never|without|skip|avoid|decline|reject|"
    r"don't|dont|do not|doesn't|does not|didn't|did not|"
    r"can't|cannot|can not|won't|will not|haven't|have not|hasn't|has not|"
    r"isn't|is not|aren't|are not|wasn't|was not|weren't|were not)\b",
    flags=re.IGNORECASE,
)
CLAUSE_BOUNDARY_RE = re.compile(
    r"[.!?;]|\b(?:but|however|instead|rather|except|unless|although)\b",
    flags=re.IGNORECASE,
)
COUNTERFACTUAL_PREFIX_RE = re.compile(
    r"(?:\b(?:if|would|could|hypothetically|suppose|assuming)\b)\W*$",
    flags=re.IGNORECASE,
)


def normalized_casefold(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def word_boundary_pattern(term: str) -> Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", flags=re.IGNORECASE)


def phrase_pattern(phrase: str) -> Pattern[str]:
    escaped = re.escape(phrase).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<!\w){escaped}(?!\w)", flags=re.IGNORECASE)


def iter_phrase_matches(text: str, phrases: Iterable[str]) -> Iterator[Match[str]]:
    for phrase in phrases:
        yield from phrase_pattern(phrase).finditer(text)


def spans_overlap(start: int, end: int, spans: Iterable[Span]) -> bool:
    return any(start < span.end and end > span.start for span in spans)


def match_overlaps(match: Match[str], spans: Iterable[Span]) -> bool:
    return spans_overlap(match.start(), match.end(), spans)


def match_is_negated(
    text: str,
    match: Match[str],
    negation_spans: Iterable[Span] | None = None,
    *,
    prefix_window: int = 48,
) -> bool:
    if negation_spans and match_overlaps(match, negation_spans):
        return True
    prefix = text[max(0, match.start() - prefix_window) : match.start()]
    if NEGATION_PREFIX_RE.search(prefix):
        return True
    clause_prefix = CLAUSE_BOUNDARY_RE.split(prefix)[-1]
    return bool(NEGATION_CUE_RE.search(clause_prefix))


def match_is_counterfactual(
    text: str,
    match: Match[str],
    counterfactual_spans: Iterable[Span] | None = None,
    *,
    prefix_window: int = 48,
) -> bool:
    if counterfactual_spans and match_overlaps(match, counterfactual_spans):
        return True
    prefix = text[max(0, match.start() - prefix_window) : match.start()]
    return bool(COUNTERFACTUAL_PREFIX_RE.search(prefix))


def has_unblocked_match(
    text: str,
    patterns: Iterable[Pattern[str]],
    *,
    negation_spans: list[Span] | None = None,
    counterfactual_spans: list[Span] | None = None,
) -> bool:
    for pattern in patterns:
        for match in pattern.finditer(text):
            if match_is_negated(text, match, negation_spans):
                continue
            if match_is_counterfactual(text, match, counterfactual_spans):
                continue
            return True
    return False
