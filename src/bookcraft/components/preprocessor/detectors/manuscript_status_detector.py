from __future__ import annotations

from bookcraft.components.preprocessor.detectors.common import (
    iter_phrase_matches,
    match_is_counterfactual,
    match_is_negated,
)
from bookcraft.components.preprocessor.schemas import Span
from bookcraft.domain.enums import ManuscriptStatus

PUBLISHED_MARKERS = ("published", "already published", "book is published")
COMPLETED_MARKERS = (
    "finished manuscript",
    "finished my manuscript",
    "finished the manuscript",
    "manuscript is finished",
    "manuscript finished",
    "completed draft",
    "complete draft",
    "draft is complete",
    "draft is finished",
    "i have finished my manuscript",
    "i've finished my manuscript",
    "i have a finished manuscript",
    "i have completed my manuscript",
    "i completed my manuscript",
)
PARTIAL_MARKERS = (
    "partial draft",
    "partially written",
    "some chapters",
    "3 chapters",
    "three chapters",
    "120 pages done",
    "pages done",
    "chapters done",
    "in progress",
    "not finished",
    "unfinished",
)
IDEA_MARKERS = (
    "idea only",
    "only have an idea",
    "just an idea",
    "starting from scratch",
    "don't have time to write",
    "do not have time to write",
    "need someone to write it",
)


def detect_manuscript_status(
    text: str,
    negation_spans: list[Span] | None = None,
    counterfactual_spans: list[Span] | None = None,
) -> ManuscriptStatus | None:
    if _has_unblocked_phrase(text, PUBLISHED_MARKERS, negation_spans, counterfactual_spans):
        return ManuscriptStatus.PUBLISHED
    if _has_unblocked_phrase(text, COMPLETED_MARKERS, negation_spans, counterfactual_spans):
        return ManuscriptStatus.COMPLETED_DRAFT
    if _has_phrase(text, PARTIAL_MARKERS):
        return ManuscriptStatus.PARTIAL_DRAFT
    if _has_phrase(text, ("outline",)):
        return ManuscriptStatus.OUTLINE
    if _has_phrase(text, IDEA_MARKERS):
        return ManuscriptStatus.IDEA_ONLY
    return None


def _has_unblocked_phrase(
    text: str,
    phrases: tuple[str, ...],
    negation_spans: list[Span] | None,
    counterfactual_spans: list[Span] | None,
) -> bool:
    return any(
        not match_is_negated(text, match, negation_spans)
        and not match_is_counterfactual(text, match, counterfactual_spans)
        for match in iter_phrase_matches(text, phrases)
    )


def _has_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    return any(iter_phrase_matches(text, phrases))
