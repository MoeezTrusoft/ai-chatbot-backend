from __future__ import annotations

from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.domain.enums import QueryIntentType, ServiceCategory


def harden_intent_from_message(
    intent: IntentVote,
    message: ProcessedMessage,
) -> IntentVote:
    """Fill conservative deterministic intent/service gaps after ensemble voting."""

    text = message.normalized.casefold()
    query = intent.query_primary
    service = intent.service_primary
    evidence = list(intent.evidence)

    detected_service = _service_from_text(text)
    if service is None and detected_service is not None:
        service = detected_service
        evidence.append(f"deterministic_service_signal:{detected_service.value}")
    elif (
        service == ServiceCategory.GHOSTWRITING
        and detected_service is not None
        and detected_service != ServiceCategory.GHOSTWRITING
        and _ghostwriting_is_negated(text)
    ):
        service = detected_service
        evidence.append(
            f"deterministic_service_override_negated_ghostwriting:{detected_service.value}"
        )

    detected_query = _query_from_text(text)

    if detected_query is not None and _should_upgrade_query(query, detected_query, text):
        query = detected_query
        evidence.append(f"deterministic_query_signal:{detected_query.value}")
    elif query == QueryIntentType.UNCLEAR and service is not None:
        query = QueryIntentType.SERVICE_QUESTION
        evidence.append("deterministic_query_signal:service_question_from_service")

    if query == intent.query_primary and service == intent.service_primary:
        return intent

    return intent.model_copy(
        update={
            "query_primary": query,
            "service_primary": service,
            "needs_clarification": query == QueryIntentType.UNCLEAR,
            "confidence": max(intent.confidence, 0.82),
            "rationale": (
                f"{intent.rationale} Deterministic hardening filled obvious "
                "BookCraft intent/service cues."
            ),
            "evidence": evidence,
        }
    )


def _should_upgrade_query(
    current: QueryIntentType,
    detected: QueryIntentType,
    text: str,
) -> bool:
    if detected == QueryIntentType.CONSULTATION_REQUEST:
        return True

    if detected in {
        QueryIntentType.PORTFOLIO_REQUEST,
        QueryIntentType.NDA_REQUEST,
        QueryIntentType.AGREEMENT_REQUEST,
    }:
        return current in {
            QueryIntentType.UNCLEAR,
            QueryIntentType.SERVICE_QUESTION,
            QueryIntentType.PRICING_QUESTION,
        }

    if detected in {
        QueryIntentType.PRICING_QUESTION,
        QueryIntentType.TIMELINE_QUESTION,
    }:
        if current == QueryIntentType.UNCLEAR:
            return True
        if current == QueryIntentType.SERVICE_QUESTION and _asks_for_estimate_or_timing(text):
            return True

    if detected == QueryIntentType.PUBLISHING_PLATFORM_QUESTION:
        return current == QueryIntentType.UNCLEAR

    return False


def _query_from_text(text: str) -> QueryIntentType | None:
    if _mentions_any(text, ["human consultant", "consultant review", "ready for a human"]):
        return QueryIntentType.CONSULTATION_REQUEST

    if _mentions_any(text, ["sample", "samples", "portfolio", "examples", "sample links"]):
        return QueryIntentType.PORTFOLIO_REQUEST

    if "nda" in text or "confidentiality" in text:
        return QueryIntentType.NDA_REQUEST

    if "agreement" in text or "service agreement" in text:
        return QueryIntentType.AGREEMENT_REQUEST

    if _asks_for_estimate_or_timing(text):
        return QueryIntentType.PRICING_QUESTION

    if _mentions_any(
        text,
        [
            "amazon kdp",
            "ingramspark",
            "isbn",
            "metadata",
            "categories",
            "keywords",
        ],
    ):
        return QueryIntentType.PUBLISHING_PLATFORM_QUESTION

    return None


def _service_from_text(text: str) -> ServiceCategory | None:
    # Order matters: prefer explicit high-signal service nouns over generic layout words.
    if _mentions_any(
        text,
        [
            "cover design",
            "cover might",
            "full illustration",
            "illustration",
            "custom typography",
            "front cover",
            "book cover",
        ],
    ):
        return ServiceCategory.COVER_DESIGN_ILLUSTRATION

    if _mentions_any(
        text,
        [
            "proofreading",
            "copy editing",
            "line editing",
            "developmental editing",
        ],
    ):
        return ServiceCategory.EDITING_PROOFREADING

    if _mentions_any(
        text,
        ["interior formatting", "formatting", "print layout", "ebook layout"],
    ):
        return ServiceCategory.INTERIOR_FORMATTING

    if _mentions_any(
        text,
        [
            "amazon kdp",
            "ingramspark",
            "isbn",
            "metadata",
            "distribution",
            "publishing",
        ],
    ):
        return ServiceCategory.PUBLISHING_DISTRIBUTION

    if _mentions_any(
        text,
        [
            "marketing campaign",
            "bestseller",
            "verified reviews",
            "media coverage",
            "ads",
        ],
    ):
        return ServiceCategory.MARKETING_PROMOTION

    if _mentions_any(
        text,
        [
            "video trailer",
            "book trailer",
            "trailer",
            "motion graphics",
            "voiceover",
        ],
    ):
        return ServiceCategory.VIDEO_TRAILER

    if _mentions_any(text, ["author website", "website", "blog", "newsletter", "lead magnet"]):
        return ServiceCategory.AUTHOR_WEBSITE

    if _mentions_any(text, ["audiobook", "narrator", "acx", "mastering", "chapter files"]):
        return ServiceCategory.AUDIOBOOK_PRODUCTION

    if "ghostwriting" in text and not _mentions_any(
        text,
        ["do not need ghostwriting", "don't need ghostwriting", "no ghostwriting"],
    ):
        return ServiceCategory.GHOSTWRITING

    return None


def _ghostwriting_is_negated(text: str) -> bool:
    return _mentions_any(
        text,
        [
            "do not need ghostwriting",
            "don't need ghostwriting",
            "no ghostwriting",
            "not ghostwriting",
            "without ghostwriting",
        ],
    )


def _asks_for_estimate_or_timing(text: str) -> bool:
    return _mentions_any(
        text,
        [
            "price",
            "pricing",
            "cost",
            "quote",
            "estimate",
            "discount",
            "payment plan",
            "timeline",
            "delivery",
            "deliver",
            "how long",
            "rush",
            "dates",
            "deterministic engine",
            "quote engine",
        ],
    )


def _mentions_any(text: str, fragments: list[str]) -> bool:
    return any(fragment in text for fragment in fragments)
