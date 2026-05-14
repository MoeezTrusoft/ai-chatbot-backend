from __future__ import annotations

from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.domain.enums import QueryIntentType, ServiceCategory


def harden_intent_from_message(
    intent: IntentVote,
    message: ProcessedMessage,
) -> IntentVote:
    """Fill conservative deterministic intent/service gaps after ensemble voting.

    The hardening layer is the last line of defense before a decision is
    persisted. It runs after the ensemble (which may have failed open or
    returned ``unclear``) and uses a curated keyword set to:

      * fill ``service_primary`` when the ensemble missed an obvious cue,
      * upgrade ``query_primary`` toward the most specific BookCraft
        intent that fits the message (consultation > portfolio/NDA/
        agreement > pricing/timeline > publishing platform),
      * never overwrite a confident, more specific signal: hardening is
        additive, not corrective in the strong sense.

    The 2026-05-14 load report found 34% of turns ending as ``unclear``
    despite messages that were trivially classifiable by a human
    reviewer. This expansion targets the recurring shapes from that
    report: indirect service cues ("marketing assets", "rewrite",
    "package my book"), service eligibility questions ("do you work
    with first-time authors"), and consultation/discovery framings
    ("suggest the best BookCraft service").
    """

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
    elif query == QueryIntentType.UNCLEAR and _has_service_discovery_cues(text):
        # Service-eligibility / advisory questions with no specific service
        # noun ("do you work with first-time authors", "suggest the best
        # BookCraft service") still belong in SERVICE_QUESTION.
        query = QueryIntentType.SERVICE_QUESTION
        evidence.append("deterministic_query_signal:service_question_from_discovery_cues")

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
        # Allow upgrade from SERVICE_QUESTION too when strong platform cues
        # are present (e.g. ISBN/KDP/IngramSpark): "Can you handle copyright
        # page and ISBN guidance?" is more specific than a generic service
        # question. The prior policy only upgraded from UNCLEAR, which left
        # platform questions misclassified whenever the ensemble correctly
        # detected SERVICE_QUESTION first.
        if current == QueryIntentType.UNCLEAR:
            return True
        if current == QueryIntentType.SERVICE_QUESTION and _has_strong_platform_cue(text):
            return True

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
            "kdp",
            "copyright page",
            "publishing platforms",
        ],
    ):
        return QueryIntentType.PUBLISHING_PLATFORM_QUESTION

    return None


def _service_from_text(text: str) -> ServiceCategory | None:
    # Order matters: prefer explicit high-signal service nouns over generic
    # layout words. Within each block, the more specific phrase appears
    # first so it can be inspected during review and tuned in isolation.

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
            "children's picture book",
            "picture book",
            "illustrated book",
        ],
    ):
        return ServiceCategory.COVER_DESIGN_ILLUSTRATION

    if _mentions_any(
        text,
        [
            "proofreading",
            "copy editing",
            "copyediting",
            "line editing",
            "developmental editing",
            # Indirect cues for editing/rewriting work. The report saw
            # several turns of the form "improve readability and flow" or
            # "human-sounding rewrite" that landed as `unclear`.
            "readability",
            "improve flow",
            "polish my",
            "polish the manuscript",
            "rewrite",
            "human-sounding",
            "human sounding",
        ],
    ):
        return ServiceCategory.EDITING_PROOFREADING

    if _mentions_any(
        text,
        [
            "interior formatting",
            "formatting",
            "print layout",
            "ebook layout",
            "epub",
            "print pdf",
            "print-ready pdf",
            "distribution-ready epub",
        ],
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
            "ebook only",
            "print only",
            "package my book",
            "multiple platforms",
            "kdp only",
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
            # Indirect cues. "blurb", "author bio", "sales copy",
            # "launch marketing assets", "lead generation" all hit
            # marketing/promotion deliverables.
            "blurb",
            "author bio",
            "sales copy",
            "launch marketing",
            "marketing assets",
            "lead generation",
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

    # Ghostwriting last because its cues are broader and more likely to
    # collide with adjacent services.
    if "ghostwriting" in text and not _ghostwriting_is_negated(text):
        return ServiceCategory.GHOSTWRITING

    if _mentions_any(
        text,
        [
            # Manuscript-completion phrasings consistently belong to
            # ghostwriting in BookCraft's catalog. "Finish a half-written
            # novel" and "turn my podcast into a book" are the canonical
            # examples from the load report.
            "finish a half-written",
            "finish my half-written",
            "finish a half written",
            "finish my novel",
            "finish my book",
            "complete my manuscript",
            "turn my podcast into a book",
            "turn my notes into a book",
            "write my book",
            "write my manuscript",
            "ghost writer",
            "ghostwriter",
            "memoir but i want my voice",
        ],
    ) and not _ghostwriting_is_negated(text):
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


def _has_service_discovery_cues(text: str) -> bool:
    """Catch service-eligibility and advisory framings.

    These messages don't name a service and aren't pricing/portfolio/
    NDA/agreement requests, but they're clearly asking about BookCraft's
    services. Routing them to SERVICE_QUESTION keeps the response flow
    in the right lane instead of dropping to UNCLEAR.
    """

    return _mentions_any(
        text,
        [
            "do you work with",
            "do you offer",
            "do you handle",
            "do you support",
            "can you help with",
            "what services",
            "best bookcraft service",
            "suggest the best",
            "explain bookcraft",
            "summarize what bookcraft",
            "review my book idea",
            "first-time author",
            "first time author",
            "next step",
            "next safe step",
        ],
    )


def _has_strong_platform_cue(text: str) -> bool:
    return _mentions_any(
        text,
        [
            "amazon kdp",
            "ingramspark",
            "isbn",
            "kdp",
            "copyright page",
            "publishing platforms",
        ],
    )


def _mentions_any(text: str, fragments: list[str]) -> bool:
    return any(fragment in text for fragment in fragments)
