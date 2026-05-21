"""GenreUncertaintyDetector — detects user uncertainty about genre without confirming it."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

_UNCERTAINTY_RE = re.compile(
    r"\b(?:don'?t\s+know|do\s+not\s+know|not\s+sure|unsure|maybe|could\s+be|"
    r"should\s+it\s+be|whether|either|between|still\s+deciding|haven'?t\s+decided|"
    r"perhaps|might\s+be|thinking(?:\s+about)?|considering|not\s+certain|"
    r"not\s+decided|undecided|on\s+the\s+fence)\b",
    flags=re.IGNORECASE,
)

_GENRE_WORDS_RE = re.compile(
    r"\b(fiction|memoir|business\s+book|business|self[- ]help|non[- ]fiction|nonfiction|"
    r"fantasy|romance|thriller|biography|children'?s?\s+book|children'?s?\s+fiction|"
    r"picture\s+book|literary\s+fiction|historical\s+fiction|mystery|horror|"
    r"personal\s+story|personal\s+narrative)\b",
    flags=re.IGNORECASE,
)

_NEGATION_PREFIX_RE = re.compile(
    r"\b(?:not|no|don'?t\s+want|don'?t\s+like|not\s+a|isn'?t|aren'?t|"
    r"without|avoid|skip)\b\s*$",
    flags=re.IGNORECASE,
)

_GENRE_NORMALISATION: dict[str, str] = {
    "self help": "self-help",
    "self-help": "self-help",
    "non fiction": "non-fiction",
    "non-fiction": "non-fiction",
    "nonfiction": "non-fiction",
    "children’s book": "children's book",
    "childrens book": "children's book",
    "children’s fiction": "children's fiction",
    "childrens fiction": "children's fiction",
    "picture book": "picture_book",
    "historical fiction": "historical fiction",
    "literary fiction": "literary fiction",
    "personal story": "memoir",
    "personal narrative": "memoir",
    "business book": "business",
}


class GenreUncertaintyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uncertain: bool = False
    genre_candidates: list[str] = Field(default_factory=list)
    negated_genres: list[str] = Field(default_factory=list)
    audit: list[str] = Field(default_factory=list)


def detect_genre_uncertainty(text: str) -> GenreUncertaintyResult:
    """
    Detect when a user expresses uncertainty about genre.

    Returns uncertain=True with candidate list when uncertainty cues are present.
    Does NOT confirm any genre. Negated genres are tracked separately.
    If uncertain, callers must not write a confirmed genre to state.
    """
    audit: list[str] = []
    has_uncertainty = bool(_UNCERTAINTY_RE.search(text))
    audit.append(f"uncertainty_cue:{has_uncertainty}")

    if not has_uncertainty:
        return GenreUncertaintyResult(audit=audit)

    candidates: list[str] = []
    negated: list[str] = []

    for match in _GENRE_WORDS_RE.finditer(text):
        raw = match.group(0).casefold().strip()
        genre = _GENRE_NORMALISATION.get(raw, raw)
        prefix = text[max(0, match.start() - 48) : match.start()]
        if _NEGATION_PREFIX_RE.search(prefix):
            if genre not in negated:
                negated.append(genre)
            audit.append(f"negated_genre:{genre}")
        else:
            if genre not in candidates:
                candidates.append(genre)
            audit.append(f"candidate_genre:{genre}")

    # Remove negated from candidates
    candidates = [g for g in candidates if g not in negated]

    return GenreUncertaintyResult(
        uncertain=True,
        genre_candidates=candidates,
        negated_genres=negated,
        audit=audit,
    )
