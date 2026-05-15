from __future__ import annotations

import re

from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.domain.enums import QueryIntentType, ServiceCategory

from .schemas import TriMatchDimension, TriMatchEvidence

_HELP_GREETING_RULE_PREFIX = "QUERY-GREETING"
_HELP_OPENER_RE = re.compile(
    r"\b(can you help(?: me)?|i need help|need help)\b",
    flags=re.IGNORECASE,
)
_GREETING_ONLY_RE = re.compile(
    r"^\s*(hi|hello|hey|good (morning|afternoon|evening))[!.?]*\s*$",
    flags=re.IGNORECASE,
)
_SIMPLE_TERMS_RE = re.compile(
    r"\b(simple|plain|easy|basic|clear)\s+terms\b|\bin\s+simple\s+terms\b",
    flags=re.IGNORECASE,
)


def apply_context_arbitration(
    evidence: list[TriMatchEvidence],
    message: ProcessedMessage,
) -> list[TriMatchEvidence]:
    """Apply safe context arbitration after raw Tri-Match matching.

    This is intentionally conservative. It removes known broad overfires while
    preserving high-specificity evidence for the same message.

    It does not parse or depend on the staged sidecar JSON yet. The staged
    sidecar remains advisory until the preprocessor/context upgrade is complete.
    """

    if not evidence:
        return evidence

    text = f"{message.normalized} {message.raw}"

    has_video_trailer = any(
        item.dimension == TriMatchDimension.SERVICE_INTENT
        and item.target == ServiceCategory.VIDEO_TRAILER.value
        and "trailer" in item.matched_text.casefold()
        for item in evidence
    )

    greeting_only = bool(_GREETING_ONLY_RE.fullmatch(message.normalized.strip()))
    help_opener = bool(_HELP_OPENER_RE.search(text))
    simple_terms_context = bool(_SIMPLE_TERMS_RE.search(text))

    filtered: list[TriMatchEvidence] = []

    for item in evidence:
        if _should_suppress_create_book_ghostwriting(item, has_video_trailer):
            continue

        if _should_suppress_help_opener_greeting(item, help_opener, greeting_only):
            continue

        if _should_suppress_simple_terms_agreement(item, simple_terms_context):
            continue

        filtered.append(item)

    return filtered


def _should_suppress_create_book_ghostwriting(
    item: TriMatchEvidence,
    has_video_trailer: bool,
) -> bool:
    if not has_video_trailer:
        return False

    return (
        item.dimension == TriMatchDimension.SERVICE_INTENT
        and item.target == ServiceCategory.GHOSTWRITING.value
        and (
            item.rule_id == "SERVICE-GHOST-RX-038"
            or item.matched_text.casefold() == "create a book"
        )
    )


def _should_suppress_help_opener_greeting(
    item: TriMatchEvidence,
    help_opener: bool,
    greeting_only: bool,
) -> bool:
    if not help_opener or greeting_only:
        return False

    return (
        item.dimension == TriMatchDimension.QUERY_INTENT
        and item.target == QueryIntentType.GREETING.value
        and item.rule_id.startswith(_HELP_GREETING_RULE_PREFIX)
    )


def _should_suppress_simple_terms_agreement(
    item: TriMatchEvidence,
    simple_terms_context: bool,
) -> bool:
    if not simple_terms_context:
        return False

    return (
        item.dimension == TriMatchDimension.QUERY_INTENT
        and item.target == QueryIntentType.AGREEMENT_REQUEST.value
        and "terms" in item.matched_text.casefold()
    )
