from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.components.context.schemas import ContextPack
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.detectors.document_request_detector import (
    has_agreement_request,
    has_nda_request,
)
from bookcraft.components.preprocessor.detectors.pricing_detector import has_pricing_intent
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.domain.enums import QueryIntentType, ServiceCategory
from bookcraft.domain.state import ThreadState

_SWITCH_MARKERS = [
    "instead",
    "actually i need",
    "actually, i need",
    "actually i want",
    "actually, i want",
    "forget",
    "change to",
    "switch to",
    "switch from",
    "rather than",
    "i changed my mind",
    "not that",
    "no longer need",
    "drop the",
    "cancel the",
]

_ADDITIVE_MARKERS = [
    "also",
    "as well",
    "plus",
    "and also",
    "can you also",
    "in addition",
    "additionally",
    "alongside",
    "on top of that",
    "together with",
    "along with",
    "in addition to",
]

_NDA_WORD_RE = re.compile(r"\bnda\b", re.IGNORECASE)
_AGREEMENT_WORD_RE = re.compile(r"\b(agreement|contract)\b", re.IGNORECASE)


class ContextArbiterResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: IntentVote
    corrections: list[str] = Field(default_factory=list)
    audit: list[str] = Field(default_factory=list)


class ContextArbiter:
    def arbitrate(
        self,
        *,
        intent: IntentVote,
        processed: ProcessedMessage,
        state: ThreadState,
        context_pack: ContextPack | None = None,
    ) -> ContextArbiterResult:
        corrections: list[str] = []
        audit: list[str] = []

        intent = _arbitrate_service(intent, processed, state, corrections, audit)
        intent = _veto_negated_pricing(intent, processed, corrections, audit)
        intent = _veto_negated_document(intent, processed, corrections, audit)
        intent = _apply_negation_targets(intent, processed, corrections, audit)
        _audit_known_facts(state, context_pack, audit)

        return ContextArbiterResult(intent=intent, corrections=corrections, audit=audit)


# ---------------------------------------------------------------------------
# Service arbitration
# ---------------------------------------------------------------------------


def _arbitrate_service(
    intent: IntentVote,
    processed: ProcessedMessage,
    state: ThreadState,
    corrections: list[str],
    audit: list[str],
) -> IntentVote:
    explicit_services = _explicit_services_from_message(processed)
    active_service = _active_service_from_state(state)

    if active_service is None:
        audit.append("service_arbiter:no_active_service")
        return intent

    if not explicit_services:
        # Pure inertia: no service mentioned in this turn.
        if intent.service_primary == active_service:
            audit.append("service_arbiter:already_correct")
            return intent
        corrections.append(
            f"state_service_inertia:"
            f"{getattr(intent.service_primary, 'value', intent.service_primary)}"
            f"→{active_service.value}"
        )
        evidence = list(intent.evidence)
        # Preserve the Phase-2 tag for backward compatibility; add the
        # arbiter-prefixed tag so callers can filter by component origin.
        if "state_service_inertia" not in evidence:
            evidence.append("state_service_inertia")
        if "context_arbiter_service_inertia" not in evidence:
            evidence.append("context_arbiter_service_inertia")
        return intent.model_copy(
            update={
                "service_primary": active_service,
                "service_secondary": [],
                "rationale": f"{intent.rationale} Service focus retained from thread state.",
                "evidence": evidence,
            }
        )

    # Explicit service(s) mentioned in this turn.
    lowered = processed.normalized.casefold()

    if _is_additive_request(lowered):
        # Preserve active service as primary; new service(s) become secondary.
        new_secondary = [s for s in explicit_services if s != active_service]
        corrections.append(f"additive_service:{','.join(s.value for s in new_secondary)}→secondary")
        audit.append("service_arbiter:additive_request_detected")
        return intent.model_copy(
            update={
                "service_primary": active_service,
                "service_secondary": new_secondary,
                "rationale": (
                    f"{intent.rationale} Additive service request; active focus retained."
                ),
            }
        )

    if _is_explicit_switch(lowered):
        audit.append("explicit_service_switch")
        return intent  # allow switch through unchanged

    # Plain service mention — also allow through (user named a service explicitly).
    audit.append("service_arbiter:explicit_service_mention_pass_through")
    return intent


# ---------------------------------------------------------------------------
# Pricing veto
# ---------------------------------------------------------------------------


def _veto_negated_pricing(
    intent: IntentVote,
    processed: ProcessedMessage,
    corrections: list[str],
    audit: list[str],
) -> IntentVote:
    if intent.query_primary not in {
        QueryIntentType.PRICING_QUESTION,
        QueryIntentType.TIMELINE_QUESTION,
    }:
        return intent

    if has_pricing_intent(
        processed.normalized,
        negation_spans=processed.negation_spans,
        counterfactual_spans=processed.counterfactual_spans,
    ):
        audit.append("pricing_arbiter:real_pricing_preserved")
        return intent

    # Pricing keyword present but negated/counterfactual → downgrade.
    fallback = (
        QueryIntentType.SERVICE_QUESTION
        if intent.service_primary is not None
        else QueryIntentType.UNCLEAR
    )
    corrections.append(f"pricing_vetoed:negated_or_counterfactual→{fallback.value}")
    audit.append("pricing_negation_veto")
    evidence = [*intent.evidence, "context_arbiter_pricing_veto"]
    return intent.model_copy(
        update={
            "query_primary": fallback,
            "needs_clarification": True,
            "rationale": f"{intent.rationale} Pricing intent vetoed: negated or counterfactual.",
            "evidence": evidence,
        }
    )


# ---------------------------------------------------------------------------
# Document veto
# ---------------------------------------------------------------------------


def _veto_negated_document(
    intent: IntentVote,
    processed: ProcessedMessage,
    corrections: list[str],
    audit: list[str],
) -> IntentVote:
    text = processed.normalized
    negation_spans = processed.negation_spans
    counterfactual_spans = processed.counterfactual_spans

    if intent.query_primary == QueryIntentType.NDA_REQUEST:
        if _NDA_WORD_RE.search(text) and not has_nda_request(
            text,
            negation_spans=negation_spans,
            counterfactual_spans=counterfactual_spans,
        ):
            corrections.append("nda_request_vetoed:negated_or_counterfactual")
            evidence = [*intent.evidence, "context_arbiter_document_veto"]
            return intent.model_copy(
                update={
                    "query_primary": QueryIntentType.SERVICE_QUESTION,
                    "needs_clarification": True,
                    "rationale": (
                        f"{intent.rationale} NDA request vetoed: negated or counterfactual."
                    ),
                    "evidence": evidence,
                }
            )
        audit.append("document_arbiter:nda_request_valid")

    if intent.query_primary == QueryIntentType.AGREEMENT_REQUEST:
        if _AGREEMENT_WORD_RE.search(text) and not has_agreement_request(
            text,
            negation_spans=negation_spans,
            counterfactual_spans=counterfactual_spans,
        ):
            corrections.append("agreement_request_vetoed:negated_or_counterfactual")
            evidence = [*intent.evidence, "context_arbiter_document_veto"]
            return intent.model_copy(
                update={
                    "query_primary": QueryIntentType.SERVICE_QUESTION,
                    "needs_clarification": True,
                    "rationale": (
                        f"{intent.rationale} Agreement request vetoed: negated or counterfactual."
                    ),
                    "evidence": evidence,
                }
            )
        audit.append("document_arbiter:agreement_request_valid")

    return intent


# ---------------------------------------------------------------------------
# Negation-target arbitration — uses NegationTarget list from ProcessedMessage
# ---------------------------------------------------------------------------


def _apply_negation_targets(
    intent: IntentVote,
    processed: ProcessedMessage,
    corrections: list[str],
    audit: list[str],
) -> IntentVote:
    """Use structured negation targets to fix service / query swaps.

    Runs after the existing veto passes so it can repair over-vetoed intents
    (e.g. NDA→SERVICE_QUESTION when agreement is the real replacement).
    """
    neg_targets = list(getattr(processed, "negation_targets", None) or [])
    if not neg_targets:
        audit.append("negation_targets:none")
        return intent

    negated: dict[str, set[str]] = {}  # target_type → set of negated values
    replacements: dict[str, set[str]] = {}  # target_type → set of replacement/affirmed values

    for t in neg_targets:
        tt = t.target_type
        tv = t.target
        p = t.polarity
        if p == "negated":
            negated.setdefault(tt, set()).add(tv)
        elif p in ("affirmed", "replacement"):
            replacements.setdefault(tt, set()).add(tv)

    audit.append(f"negation_targets:negated={dict(negated)},replacements={dict(replacements)}")

    # --- Service swap ---
    neg_services = negated.get("service", set())
    rep_services = replacements.get("service", set())

    if neg_services and rep_services:
        current_svc = intent.service_primary
        current_svc_val = current_svc.value if current_svc else None
        # If current primary is negated, replace with the affirmed service.
        if current_svc_val in neg_services or current_svc is None:
            for svc_val in rep_services:
                try:
                    new_svc = ServiceCategory(svc_val)
                    corrections.append(f"negation_target_service_swap:{current_svc_val}→{svc_val}")
                    new_secondary = [
                        s for s in intent.service_secondary if s.value not in neg_services
                    ]
                    return intent.model_copy(
                        update={
                            "service_primary": new_svc,
                            "service_secondary": new_secondary,
                            "evidence": [*intent.evidence, "negation_target_service_replacement"],
                        }
                    )
                except ValueError:
                    pass
    elif neg_services:
        # Service negated with no replacement → remove from primary/secondary.
        current_svc = intent.service_primary
        if current_svc is not None and current_svc.value in neg_services:
            non_neg_secondary = [s for s in intent.service_secondary if s.value not in neg_services]
            new_primary = non_neg_secondary[0] if non_neg_secondary else None
            new_secondary = non_neg_secondary[1:] if non_neg_secondary else []
            corrections.append(f"negation_target_service_removed:{current_svc.value}")
            intent = intent.model_copy(
                update={
                    "service_primary": new_primary,
                    "service_secondary": new_secondary,
                    "evidence": [*intent.evidence, "negation_target_service_removal"],
                }
            )

    # --- Document / tool-action swap ---
    neg_actions = negated.get("tool_action", set()) | negated.get("document", set())
    rep_actions = replacements.get("tool_action", set()) | replacements.get("document", set())

    # NDA negated + agreement affirmed → upgrade intent to AGREEMENT_REQUEST.
    if "generate_nda" in neg_actions or "nda" in neg_actions:
        if "generate_agreement" in rep_actions or "agreement" in rep_actions:
            if intent.query_primary != QueryIntentType.AGREEMENT_REQUEST:
                corrections.append(
                    "negation_target:nda_negated_agreement_affirmed→agreement_request"
                )
                intent = intent.model_copy(
                    update={
                        "query_primary": QueryIntentType.AGREEMENT_REQUEST,
                        "needs_clarification": False,
                        "evidence": [*intent.evidence, "negation_target_nda_to_agreement"],
                    }
                )

    # Pricing negated + portfolio affirmed → upgrade intent to PORTFOLIO_REQUEST.
    if "price_quote" in neg_actions or "pricing" in neg_actions:
        if "portfolio_lookup" in rep_actions:
            if intent.query_primary not in {
                QueryIntentType.PORTFOLIO_REQUEST,
            }:
                corrections.append(
                    "negation_target:price_quote_negated_portfolio_affirmed→portfolio_request"
                )
                intent = intent.model_copy(
                    update={
                        "query_primary": QueryIntentType.PORTFOLIO_REQUEST,
                        "needs_clarification": False,
                        "evidence": [*intent.evidence, "negation_target_pricing_to_portfolio"],
                    }
                )

    return intent


# ---------------------------------------------------------------------------
# Known-fact audit (no intent mutations, informational only)
# ---------------------------------------------------------------------------


def _audit_known_facts(
    state: ThreadState,
    context_pack: ContextPack | None,
    audit: list[str],
) -> None:
    known: list[str] = []
    if state.project.genre.value is not None:
        known.append(f"genre:{state.project.genre.value}")
    if state.project.manuscript_status.value is not None:
        known.append(f"manuscript_status:{state.project.manuscript_status.value}")
    if state.project.word_count.value is not None:
        known.append(f"word_count:{state.project.word_count.value}")
    elif state.project.page_count.value is not None:
        known.append(f"page_count:{state.project.page_count.value}")

    if known:
        audit.append("known_facts:" + ";".join(known))

    if context_pack is not None and context_pack.forbidden_reasks:
        audit.append("forbidden_reasks:" + ",".join(context_pack.forbidden_reasks))


# ---------------------------------------------------------------------------
# Helpers (private, not re-exported)
# ---------------------------------------------------------------------------


def _explicit_services_from_message(processed: ProcessedMessage) -> list[ServiceCategory]:
    raw_services = processed.deterministic_atoms.get("services") or []
    if not isinstance(raw_services, list):
        return []
    services: list[ServiceCategory] = []
    for raw in raw_services:
        if not isinstance(raw, str):
            continue
        try:
            svc = ServiceCategory(raw)
        except ValueError:
            continue
        if svc not in services:
            services.append(svc)
    return services


def _active_service_from_state(state: ThreadState) -> ServiceCategory | None:
    for interest in reversed(state.project.services_discussed):
        raw = interest.service.value
        if isinstance(raw, ServiceCategory):
            return raw
        if isinstance(raw, str):
            try:
                return ServiceCategory(raw)
            except ValueError:
                continue
    return None


def _is_additive_request(lowered: str) -> bool:
    return any(marker in lowered for marker in _ADDITIVE_MARKERS)


def _is_explicit_switch(lowered: str) -> bool:
    return any(marker in lowered for marker in _SWITCH_MARKERS)
