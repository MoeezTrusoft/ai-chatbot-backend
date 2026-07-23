from __future__ import annotations

import re

from bookcraft.components.preprocessor.detectors.common import (
    iter_phrase_matches,
    match_is_counterfactual,
    match_is_negated,
)
from bookcraft.components.preprocessor.schemas import Span

# "novel" and "story" are ambiguous: besides the fiction genre they are everyday
# words — "novel" is a common adjective ("a novel approach/idea") and "story"
# appears in idioms ("my life story", "long story short") that carry no genre
# signal. When the *fiction* rule fires on a bare "novel"/"story" (never on the
# unambiguous word "fiction"), these guards reject the non-genre uses. The check
# is match-relative so a real cue elsewhere in the text is unaffected.
_NOVEL_ADJECTIVE_AFTER_RE = re.compile(
    r"^\W+(?:approach|idea|ideas|way|ways|concept|concepts|method|methods|"
    r"solution|solutions|technique|techniques|design|designs|angle|angles|"
    r"twist|twists|use|uses|application|applications|feature|features|"
    r"invention|inventions|coronavirus|virus)\b",
    flags=re.IGNORECASE,
)
_STORY_IDIOM_BEFORE_RE = re.compile(
    r"\b(?:life|long|back|cover|success|sob|whole)\W*$",
    flags=re.IGNORECASE,
)
_STORY_IDIOM_AFTER_RE = re.compile(
    r"^\W+short\b",
    flags=re.IGNORECASE,
)


def _fiction_cue_is_idiomatic(text: str, match: re.Match[str]) -> bool:
    """True when a fiction match landed on an adjectival/idiomatic use.

    Only bare "novel"/"story" are ambiguous; the literal word "fiction" always
    signals genre and is never treated as idiomatic here.
    """
    word = match.group().casefold()
    if word == "novel":
        return bool(_NOVEL_ADJECTIVE_AFTER_RE.match(text[match.end() :]))
    if word == "story":
        before = text[: match.start()]
        after = text[match.end() :]
        return bool(
            _STORY_IDIOM_BEFORE_RE.search(before)
            or _STORY_IDIOM_AFTER_RE.match(after)
        )
    return False

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
            if genre == "fiction" and _fiction_cue_is_idiomatic(text, match):
                continue
            return genre
    return None
