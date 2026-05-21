"""AssumptionGuard — prevents unconfirmed facts from being asserted as confirmed."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.components.context.schemas import ContextPack

Certainty = Literal["confirmed", "candidate", "uncertain", "negated", "delegated", "unknown"]

# Paths that require explicit confirmation before being stated as established facts.
_GUARDED_PATHS: frozenset[str] = frozenset(
    {
        "project.genre",
        "project.manuscript_status",
        "project.word_count",
        "project.page_count",
        "project.audience",
    }
)

# Patterns for assumption leakage — unconfirmed genre asserted as established.
# Targets strong establishment language only; not general references like "your fiction book".
_ESTABLISHED_LEAK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bwe'?ve\s+established\b", re.IGNORECASE),
    re.compile(r"\bwe\s+(?:already\s+)?know\s+(?:that\s+)?this\s+is\b", re.IGNORECASE),
    re.compile(r"\bthis\s+is\s+(?:a\s+)?memoir\b", re.IGNORECASE),
    re.compile(r"\b(?:memoir|fiction|business)-leaning\b", re.IGNORECASE),
    re.compile(
        r"\bsince\s+you'?re?\s+(?:writing|doing|working\s+on)\s+(?:a\s+)?(?:memoir|fiction|business)\b",
        re.IGNORECASE,
    ),
]

# "picture book" assumed to be children's without audience evidence.
_PICTURE_BOOK_CHILDREN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bchildren'?s?\s+(?:picture\s+book|book)\b", re.IGNORECASE),
    re.compile(r"\bpicture\s+book\s+for\s+(?:children|kids)\b", re.IGNORECASE),
]

# Scoping words that should not appear in a greeting-only response.
_GREETING_SCOPING_RE = re.compile(
    r"\b(?:word\s+count|page\s+count|how\s+many\s+(?:words|pages)|"
    r"genre|manuscript\s+stage|what\s+stage|starting\s+from\s+scratch)\b",
    re.IGNORECASE,
)


class AssumptionFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_path: str
    candidate_value: Any | None = None
    certainty: Certainty = "unknown"
    reason: str = ""
    audit: list[str] = Field(default_factory=list)


class AssumptionGuard:
    """
    Guard against unconfirmed facts becoming confirmed facts in customer-facing responses.

    Integrates with ResponseQualityGate to detect assumption leaks.
    """

    def check_response(
        self,
        *,
        text: str,
        context_pack: ContextPack | None = None,
        response_plan: Any | None = None,
    ) -> list[str]:
        """
        Check response text for assumption leaks and scoping violations.

        Returns list of failure labels (empty if clean).
        """
        failures: list[str] = []
        genre_confirmed = _is_fact_confirmed("project.genre", context_pack)

        # Established-fact leak: asserting confirmed state without evidence.
        for pattern in _ESTABLISHED_LEAK_PATTERNS:
            if pattern.search(text):
                if not genre_confirmed:
                    failures.append(
                        f"assumption_leak:established_unconfirmed:{pattern.pattern[:40]}"
                    )
                break

        # Greeting scoping violation.
        primary_goal = getattr(response_plan, "primary_goal", None) if response_plan else None
        if primary_goal == "greeting_welcome" and _GREETING_SCOPING_RE.search(text):
            failures.append("greeting_asked_scoping_question")

        # Picture book → children's assumption without audience evidence.
        book_formats = _get_context_book_formats(context_pack)
        has_audience_evidence = _has_audience_evidence(context_pack)
        if "picture_book" in book_formats and not has_audience_evidence:
            for pattern in _PICTURE_BOOK_CHILDREN_PATTERNS:
                if pattern.search(text):
                    failures.append("assumption_leak:picture_book_assumed_children_genre")
                    break

        return failures

    def evaluate_delta(
        self,
        *,
        fact_path: str,
        candidate_value: Any,
        context_pack: ContextPack | None = None,
        genre_status: str | None = None,
        genre_candidates: list[str] | None = None,
    ) -> AssumptionFact:
        """
        Evaluate whether a state delta should be allowed or blocked.

        Returns an AssumptionFact with the certainty level.
        Callers must check certainty != 'confirmed' before writing to confirmed state.
        """
        audit: list[str] = []

        if fact_path not in _GUARDED_PATHS:
            return AssumptionFact(
                fact_path=fact_path,
                candidate_value=candidate_value,
                certainty="confirmed",
                reason="unguarded_path",
                audit=["path_not_guarded"],
            )

        if fact_path == "project.genre":
            if genre_status == "uncertain":
                audit.append("genre_uncertain:blocked")
                return AssumptionFact(
                    fact_path=fact_path,
                    candidate_value=candidate_value,
                    certainty="uncertain",
                    reason="genre_status_uncertain",
                    audit=audit,
                )
            if genre_candidates and candidate_value in genre_candidates:
                audit.append(f"genre_candidate:{candidate_value}")
                return AssumptionFact(
                    fact_path=fact_path,
                    candidate_value=candidate_value,
                    certainty="candidate",
                    reason="in_candidates_not_confirmed",
                    audit=audit,
                )
            if _is_fact_confirmed(fact_path, context_pack):
                audit.append("genre_confirmed")
                return AssumptionFact(
                    fact_path=fact_path,
                    candidate_value=candidate_value,
                    certainty="confirmed",
                    reason="confirmed_in_context",
                    audit=audit,
                )

        return AssumptionFact(
            fact_path=fact_path,
            candidate_value=candidate_value,
            certainty="unknown",
            reason="insufficient_evidence",
            audit=audit,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_fact_confirmed(fact_path: str, context_pack: ContextPack | None) -> bool:
    if context_pack is None:
        return False
    return any(
        fact.path == fact_path and fact.confidence >= 0.7 for fact in context_pack.known_facts
    )


def _get_context_book_formats(context_pack: ContextPack | None) -> list[str]:
    if context_pack is None:
        return []
    return list(getattr(context_pack, "book_formats", None) or [])


def _has_audience_evidence(context_pack: ContextPack | None) -> bool:
    if context_pack is None:
        return False
    audience = getattr(context_pack, "audience", None)
    if audience:
        return True
    # Check known_facts for audience.
    return any(fact.path == "project.audience" for fact in context_pack.known_facts)
