from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.components.context.schemas import ContextPack
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.detectors.document_request_detector import (
    has_agreement_request,
    has_nda_request,
)
from bookcraft.components.preprocessor.detectors.pricing_detector import has_pricing_intent
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.domain.state import ThreadState

# Intent confidence threshold below which side-effect actions are blocked.
_SIDE_EFFECT_MIN_CONFIDENCE: float = 0.60

_SIDE_EFFECT_ACTIONS: frozenset[str] = frozenset(
    {
        "create_lead",
        "schedule_consultation",
        "generate_nda",
        "generate_agreement",
        "price_quote",
    }
)
_READ_ONLY_ACTIONS: frozenset[str] = frozenset({"portfolio_lookup"})

_NDA_WORD_RE = re.compile(r"\bnda\b", re.IGNORECASE)
_AGREEMENT_WORD_RE = re.compile(r"\b(agreement|contract)\b", re.IGNORECASE)


class ToolGovernanceDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    requires_confirmation: bool = False
    reason: str
    idempotency_key: str | None = None
    blocked_message: str | None = None
    audit: list[str] = Field(default_factory=list)


class ToolGovernanceGate:
    """Centralised governance layer that decides whether a planned action may run.

    Always returns a ToolGovernanceDecision; never raises. Blocking decisions carry
    a customer-safe blocked_message with no internal implementation details.
    """

    def evaluate(
        self,
        *,
        action_plan: Any,
        intent: IntentVote,
        processed: ProcessedMessage,
        state: ThreadState,
        context_pack: ContextPack | None = None,
        thread_id: UUID | None = None,
    ) -> ToolGovernanceDecision:
        del context_pack  # reserved for future use
        audit: list[str] = []

        at = _action_type(action_plan)

        # Rule 1 — No planned action.
        if at is None:
            audit.append("governance:no_action")
            return ToolGovernanceDecision(allowed=True, reason="no_action", audit=audit)

        # Not-needed status is also a no-action.
        status = _status_str(action_plan)
        if status == "not_needed":
            audit.append("governance:no_action")
            return ToolGovernanceDecision(allowed=True, reason="no_action", audit=audit)

        # Read-only actions are always safe.
        if _is_read_only_action(at):
            audit.append(f"governance:read_only:{at}")
            return ToolGovernanceDecision(
                allowed=True,
                reason="read_only_allowed",
                audit=audit,
            )

        # Non-READY status: dispatcher will not execute; allow for trace continuity.
        if status != "ready":
            if status in {"missing_info", "needs_confirmation"}:
                audit.append(f"governance:non_ready:{status}")
                return ToolGovernanceDecision(
                    allowed=True,
                    reason="missing_info_allowed",
                    audit=audit,
                )
            # BLOCKED or unknown: planner already stopped execution.
            audit.append(f"governance:planner_{status}")
            return ToolGovernanceDecision(
                allowed=True,
                reason=f"planner_{status}_passthrough",
                audit=audit,
            )

        # --- status == READY; potential side-effect ---

        # Rule 2 — Low confidence blocks side-effect actions.
        if _is_side_effect_action(at) and intent.confidence < _SIDE_EFFECT_MIN_CONFIDENCE:
            audit.append(f"governance:low_confidence:{intent.confidence:.2f}")
            return _blocked(
                reason="low_confidence_side_effect_blocked",
                message="I should confirm a few details before moving ahead with that.",
                audit=audit,
            )

        # Rule 3 — Counterfactual turns block side-effect actions.
        # NDA and agreement have dedicated checks that also handle counterfactuals
        # via has_nda_request / has_agreement_request; skip the generic check for those.
        if (
            _is_side_effect_action(at)
            and at not in {"generate_nda", "generate_agreement"}
            and processed.counterfactual_spans
        ):
            audit.append("governance:counterfactual_signal")
            return _blocked(
                reason="counterfactual_side_effect_blocked",
                message="I can help with that, but I need the required details first.",
                audit=audit,
            )

        # Rule 3/5 — NDA negation / counterfactual defense-in-depth.
        if at == "generate_nda":
            decision = _gate_nda(processed, audit)
            if decision is not None:
                return decision

        # Rule 3/6 — Agreement negation / prerequisite defense-in-depth.
        if at == "generate_agreement":
            decision = _gate_agreement(processed, state, audit)
            if decision is not None:
                return decision

        # Rule 8/9 — Pricing: block only if clearly negated.
        if at == "price_quote":
            decision = _gate_pricing(processed, audit)
            if decision is not None:
                return decision

        # Required-slots defense-in-depth.
        if not _has_required_slots(action_plan, state, at):
            audit.append("governance:missing_required_slots")
            return _blocked(
                reason="missing_required_slots",
                message="I can help with that, but I need the required details first.",
                audit=audit,
            )

        # Allowed — generate idempotency key for write actions.
        idem_key = _idempotency_key(action_plan, thread_id=thread_id)
        audit.append(f"governance:allowed:{at}")
        return ToolGovernanceDecision(
            allowed=True,
            requires_confirmation=bool(getattr(action_plan, "confirmation_required", False)),
            reason="allowed_with_idempotency_key",
            idempotency_key=idem_key,
            audit=audit,
        )


# ---------------------------------------------------------------------------
# Per-action defense-in-depth checks
# ---------------------------------------------------------------------------


def _gate_nda(
    processed: ProcessedMessage,
    audit: list[str],
) -> ToolGovernanceDecision | None:
    text = processed.normalized
    nda_present = bool(_NDA_WORD_RE.search(text))
    if not nda_present:
        # Confirmation turn — no "nda" word in text; safe to proceed.
        audit.append("governance:nda_word_absent_in_turn")
        return None

    if not has_nda_request(
        text,
        negation_spans=processed.negation_spans,
        counterfactual_spans=processed.counterfactual_spans,
    ):
        audit.append("governance:nda_negated_or_counterfactual")
        return _blocked(
            reason="negated_nda_blocked",
            message="I should confirm a few details before moving ahead with that.",
            audit=audit,
        )

    audit.append("governance:nda_request_valid")
    return None


def _gate_agreement(
    processed: ProcessedMessage,
    state: ThreadState,
    audit: list[str],
) -> ToolGovernanceDecision | None:
    text = processed.normalized
    agreement_present = bool(_AGREEMENT_WORD_RE.search(text))

    if agreement_present and not has_agreement_request(
        text,
        negation_spans=processed.negation_spans,
        counterfactual_spans=processed.counterfactual_spans,
    ):
        audit.append("governance:agreement_negated_or_counterfactual")
        return _blocked(
            reason="negated_agreement_blocked",
            message="I should confirm a few details before moving ahead with that.",
            audit=audit,
        )

    # Defense-in-depth: agreement requires an existing approved quote.
    if not state.sales_actions.pricing.quote_id:
        audit.append("governance:agreement_no_approved_quote")
        return _blocked(
            reason="agreement_requires_approved_quote",
            message=("I can help with that, but I need the required details first."),
            audit=audit,
        )

    audit.append("governance:agreement_request_valid")
    return None


def _gate_pricing(
    processed: ProcessedMessage,
    audit: list[str],
) -> ToolGovernanceDecision | None:
    if not has_pricing_intent(
        processed.normalized,
        negation_spans=processed.negation_spans,
        counterfactual_spans=processed.counterfactual_spans,
    ):
        audit.append("governance:pricing_negated_or_no_signal")
        return _blocked(
            reason="negated_pricing_blocked",
            message="I can help with that, but I need the required details first.",
            audit=audit,
        )

    audit.append("governance:pricing_signal_valid")
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _action_type(action_plan: Any) -> str | None:
    """Extract the action type as a plain string from an action plan of any shape."""
    raw = getattr(action_plan, "action_type", None)
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    # StrEnum: str(value) returns the enum value, e.g. "create_lead".
    return str(raw)


def _status_str(action_plan: Any) -> str:
    """Extract the action status as a lowercase string."""
    raw = getattr(action_plan, "status", None)
    if raw is None:
        return ""
    return str(raw).lower()


def _is_side_effect_action(action_type: str) -> bool:
    return action_type in _SIDE_EFFECT_ACTIONS


def _is_read_only_action(action_type: str) -> bool:
    return action_type in _READ_ONLY_ACTIONS


def _has_required_slots(
    action_plan: Any,
    state: ThreadState,
    action_type: str,
) -> bool:
    """Return True when all required slots for the action type are present."""
    del state, action_type  # reserved for per-action validation in future
    missing = getattr(action_plan, "missing_slots", None) or []
    return len(missing) == 0


def _idempotency_key(
    action_plan: Any,
    *,
    thread_id: UUID | None = None,
) -> str:
    """Produce a deterministic 24-char hex key for a given action + context."""
    key_data: dict[str, Any] = {
        "action_type": _action_type(action_plan),
        "slots": getattr(action_plan, "collected_slots", {}),
    }
    if thread_id is not None:
        key_data["thread_id"] = str(thread_id)
    serialized = json.dumps(key_data, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:24]


def _blocked(
    *,
    reason: str,
    message: str,
    audit: list[str],
) -> ToolGovernanceDecision:
    return ToolGovernanceDecision(
        allowed=False,
        reason=reason,
        blocked_message=message,
        audit=audit,
    )
