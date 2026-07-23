from __future__ import annotations

from bookcraft.components.preprocessor.detectors.common import (
    iter_phrase_matches,
    match_is_counterfactual,
    match_is_negated,
)
from bookcraft.components.preprocessor.schemas import Span

GENRE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("memoir", ("memoir",)),
    (
        "children's fiction",
        (
            "children's fiction",
            "children's fiction",
            "childrens fiction",
            "fiction children book",
            "fiction children's book",
            "fiction children's book",
            "children fiction book",
            "children's fiction book",
            "children's fiction book",
            "childrens fiction book",
        ),
    ),
    (
        "children's book",
        (
            "children book",
            "children's book",
            "children's book",
            "childrens book",
            "kids book",
            "kid's book",
            # "picture book" is intentionally excluded — it is a book format/type,
            # not a confirmed genre. Use BookFormatDetector for format detection.
        ),
    ),
    ("fantasy", ("fantasy",)),
    ("romance", ("romance",)),
    ("thriller", ("thriller",)),
    ("business", ("business book", "business")),
    ("non-fiction", ("non-fiction", "nonfiction", "non fiction")),
    ("fiction", ("fiction", "story", "novel")),
)


def detect_genre(
    text: str,
    negation_spans: list[Span] | None = None,
    counterfactual_spans: list[Span] | None = None,
) -> str | None:
    """Detect a confirmed genre, skipping negated/counterfactual mentions.

    Genre previously had NO negation guard, so "not a children's book", "no romance
    in it", "don't want a thriller" all extracted the negated genre at high confidence
    and mis-scoped the whole conversation. It now honors the same negation/
    counterfactual spans every other detector uses.
    """
    for genre, phrases in GENRE_RULES:
        for match in iter_phrase_matches(text, phrases):
            if match_is_negated(text, match, negation_spans):
                continue
            if match_is_counterfactual(text, match, counterfactual_spans):
                continue
            return genre
    return None
