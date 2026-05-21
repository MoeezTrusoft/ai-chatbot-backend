from __future__ import annotations

from bookcraft.components.preprocessor.detectors.common import phrase_pattern

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


def detect_genre(text: str) -> str | None:
    for genre, phrases in GENRE_RULES:
        if any(phrase_pattern(phrase).search(text) for phrase in phrases):
            return genre
    return None
