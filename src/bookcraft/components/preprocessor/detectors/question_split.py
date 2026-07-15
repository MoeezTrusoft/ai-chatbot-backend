"""Split a customer message into the distinct questions it actually asks.

Authors regularly paste a whole due-diligence checklist in one turn — rights,
ownership, royalties, fees — and expect every line answered. The rest of the
pipeline is built around a single question per turn: the priority classifier
returns on its first regex hit, RAG runs one truncated query, and the response
prompt asks for one short answer. The net effect was a reply that engaged with
one item and silently dropped the other twelve (chat 5876).

This detector recovers the full list so retrieval can cover every question and
the prompt can require every one of them to be answered.
"""

from __future__ import annotations

import re

# Interrogative openers. A trailing "?" alone is not enough to call a fragment a
# question ("Questions first???" is a preamble, not something to answer), and it
# is also not required — "Tell me who owns the ISBN" counts.
_INTERROGATIVE_RE = re.compile(
    r"^(?:who|whom|whose|what|when|where|why|how|which|"
    r"can|could|do|does|did|is|are|was|were|will|would|should|shall|may|might|"
    r"have|has|had|am|if|any|must|need)\b",
    re.IGNORECASE,
)

# Leading list decoration to strip: bullets, numbering, colons, dashes.
_LEADING_NOISE_RE = re.compile(r"^[\s:;\-–—•*·>»)\]\d.]+")

# A question ends at "?"; a preceding sentence ends at "." / "!" / a newline.
_QUESTION_BOUNDARY_RE = re.compile(r"\?+")
_SENTENCE_TAIL_RE = re.compile(r"(?<=[.!])\s+|\n+")

# Upper bound on questions carried forward. Well past any realistic checklist —
# it exists to bound RAG fan-out and prompt size on pathological input, not to
# trim genuine questions. Callers log when it truncates.
MAX_QUESTIONS = 15

# Below this many characters a fragment is decoration, not a question ("Any?").
_MIN_QUESTION_LEN = 8


def split_questions(text: str, *, max_questions: int = MAX_QUESTIONS) -> list[str]:
    """Return the distinct questions in ``text``, in the order they were asked.

    Returns ``[]`` when nothing parses as a question. A single-question message
    yields one entry, so callers should branch on ``len(...) >= 2`` to decide
    whether a turn needs multi-question treatment.
    """
    if not text or "?" not in text:
        # Every real multi-question turn punctuates. Requiring "?" keeps ordinary
        # declarative messages ("I need help with my cover") out of the fan-out.
        return []

    questions: list[str] = []
    seen: set[str] = set()

    for raw_segment in _QUESTION_BOUNDARY_RE.split(text):
        segment = raw_segment.strip()
        if not segment:
            continue

        # Keep only the trailing clause: "I have a draft. Who owns the ISBN"
        # should contribute the question, not the statement in front of it.
        segment = _SENTENCE_TAIL_RE.split(segment)[-1]
        segment = _LEADING_NOISE_RE.sub("", segment).strip()

        if len(segment) < _MIN_QUESTION_LEN:
            continue
        if not _INTERROGATIVE_RE.match(segment):
            continue

        key = " ".join(segment.casefold().split())
        if key in seen:
            continue
        seen.add(key)
        questions.append(f"{segment}?")

        if len(questions) >= max_questions:
            break

    return questions
