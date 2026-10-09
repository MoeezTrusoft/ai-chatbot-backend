"""Pure intent arbitration and audit policies, independent of conversation persistence."""

from __future__ import annotations

from typing import Any

from bookcraft.components.intent.schemas import IntentVote
from bookcraft.domain.enums import QueryIntentType, ServiceCategory


def _trimatch_shortcut_considered_payload(
    *,
    extra_shortcut: object,
    final_intent: IntentVote,
) -> dict[str, Any]:
    extra_snapshot = _trimatch_snapshot(extra_shortcut)
    final_snapshot = _intent_snapshot(final_intent)
    safety = _shortcut_safety_snapshot(
        extra_snapshot=extra_snapshot,
        final_snapshot=final_snapshot,
    )
    shortcut = _shortcut_candidate_decision(
        extra_shortcut=extra_shortcut,
        extra_snapshot=extra_snapshot,
        final_snapshot=final_snapshot,
        safety=safety,
    )

    return {
        "extra_shortcut": extra_snapshot,
        "final": final_snapshot,
        "shortcut": shortcut,
        "safety": safety,
    }


def _shortcut_candidate_decision(
    *,
    extra_shortcut: object,
    extra_snapshot: dict[str, Any] | None,
    final_snapshot: dict[str, Any],
    safety: dict[str, Any],
) -> dict[str, Any]:
    dimension, recommended_value = _shortcut_recommendation(extra_snapshot)
    evidence = _shortcut_evidence(extra_shortcut)
    primary_evidence = _primary_shortcut_evidence(evidence)
    rule_id = primary_evidence.get("rule_id") if isinstance(primary_evidence, dict) else None

    blocked_reasons = _shortcut_blocked_reasons(
        dimension=dimension,
        recommended_value=recommended_value,
        evidence=evidence,
        final_snapshot=final_snapshot,
        safety=safety,
    )
    eligible = not blocked_reasons

    return {
        "eligible": eligible,
        "applied": eligible,
        "dimension": dimension if eligible else None,
        "recommended_value": recommended_value if eligible else None,
        "rule_id": rule_id if eligible and isinstance(rule_id, str) else None,
        "reason": (
            "applied: safe shortcut resolved eligible intent recommendation"
            if eligible
            else "blocked: " + "; ".join(blocked_reasons)
        ),
        "blocked_reasons": blocked_reasons,
    }


def _apply_shortcut_to_intent(
    *,
    intent: IntentVote,
    shortcut: dict[str, Any],
) -> IntentVote:
    if shortcut.get("applied") is not True:
        return intent

    dimension = shortcut.get("dimension")
    recommended_value = shortcut.get("recommended_value")
    rule_id = shortcut.get("rule_id")
    if not isinstance(dimension, str) or not isinstance(recommended_value, str):
        return intent
    if not isinstance(rule_id, str) or not rule_id:
        return intent

    updates: dict[str, Any] = {}
    try:
        if dimension == "query_primary":
            updates["query_primary"] = QueryIntentType(recommended_value)
        elif dimension == "service_primary":
            updates["service_primary"] = ServiceCategory(recommended_value)
        else:
            return intent
    except ValueError:
        return intent

    evidence = [
        *intent.evidence,
        f"trimatch shortcut applied {dimension}={recommended_value} rule_id={rule_id}",
    ]
    rationale = f"{intent.rationale} Shortcut applied safely for {dimension}={recommended_value}."

    return intent.model_copy(
        update={
            **updates,
            "evidence": evidence,
            "rationale": rationale,
        }
    )


def _primary_shortcut_evidence(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    for item in evidence:
        if (
            item.get("layer") in {"exact", "regex"}
            and item.get("shortcut_eligible") is True
            and item.get("negated") is not True
            and item.get("counterfactual") is not True
        ):
            return item
    return evidence[0] if evidence else {}


def _shortcut_recommendation(
    extra_snapshot: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    if extra_snapshot is None:
        return None, None

    for dimension in ("query_primary", "service_primary"):
        value = extra_snapshot.get(dimension)
        if isinstance(value, str) and value:
            return dimension, value

    return None, None


def _shortcut_evidence(extra_shortcut: object) -> list[dict[str, Any]]:
    evidence = getattr(extra_shortcut, "evidence", [])
    if not isinstance(evidence, list):
        return []

    snapshots: list[dict[str, Any]] = []
    for item in evidence:
        if hasattr(item, "model_dump"):
            raw = item.model_dump(mode="json")
        elif isinstance(item, dict):
            raw = item
        else:
            continue

        if isinstance(raw, dict):
            snapshots.append(raw)

    return snapshots


def _shortcut_blocked_reasons(
    *,
    dimension: str | None,
    recommended_value: str | None,
    evidence: list[dict[str, Any]],
    final_snapshot: dict[str, Any],
    safety: dict[str, Any],
) -> list[str]:
    blocked: list[str] = []

    if dimension is None or recommended_value is None:
        blocked.append("no extra shortcut recommendation")
        return blocked

    if dimension not in {"query_primary", "service_primary"}:
        blocked.append(f"unsupported dimension: {dimension}")

    if recommended_value == final_snapshot.get(dimension):
        blocked.append("recommendation already matches final intent")

    if recommended_value in {
        "pricing_question",
        "timeline_question",
        "portfolio_request",
        "nda_request",
        "agreement_request",
        "payment_question",
        "complaint_or_objection",
        "ready_to_buy",
        "spam_or_abuse",
        "off_topic",
    }:
        blocked.append(f"forbidden recommended value: {recommended_value}")

    if _sensitive_shortcut_safety_blocked(safety):
        blocked.append("safety-sensitive intent cannot use shortcut")

    if not evidence:
        blocked.append("no shortcut evidence")

    allowed_evidence = [
        item
        for item in evidence
        if item.get("layer") in {"exact", "regex"}
        and item.get("shortcut_eligible") is True
        and item.get("negated") is not True
        and item.get("counterfactual") is not True
    ]

    if not allowed_evidence:
        blocked.append("no exact or regex shortcut-eligible evidence")

    if allowed_evidence and not any(
        isinstance(item.get("rule_id"), str) and item.get("rule_id") for item in allowed_evidence
    ):
        blocked.append("missing shortcut rule_id")

    if any(item.get("layer") in {"semantic", "fuzzy"} for item in evidence):
        blocked.append("semantic or fuzzy evidence cannot shortcut")

    if any(item.get("shortcut_eligible") is not True for item in evidence):
        blocked.append("shortcut_allowed false or missing on evidence")

    if any(item.get("negated") is True for item in evidence):
        blocked.append("negated evidence cannot shortcut")

    if any(item.get("counterfactual") is True for item in evidence):
        blocked.append("counterfactual evidence cannot shortcut")

    return blocked


def _sensitive_shortcut_safety_blocked(safety: dict[str, Any]) -> bool:
    return any(
        bool(safety.get(key))
        for key in (
            "pricing_sensitive",
            "document_sensitive",
            "portfolio_sensitive",
            "negated",
            "counterfactual",
            "side_effects_allowed",
        )
    )


def _shortcut_safety_snapshot(
    *,
    extra_snapshot: dict[str, Any] | None,
    final_snapshot: dict[str, Any],
) -> dict[str, Any]:
    extra_query = extra_snapshot.get("query_primary") if extra_snapshot else None
    final_query = final_snapshot.get("query_primary")

    pricing_sensitive = extra_query in {
        "pricing_question",
        "timeline_question",
        "payment_question",
    } or final_query in {
        "pricing_question",
        "timeline_question",
        "payment_question",
    }
    document_sensitive = extra_query in {
        "nda_request",
        "agreement_request",
    } or final_query in {
        "nda_request",
        "agreement_request",
    }
    portfolio_sensitive = extra_query == "portfolio_request" or final_query == "portfolio_request"

    return {
        "pricing_sensitive": pricing_sensitive,
        "document_sensitive": document_sensitive,
        "portfolio_sensitive": portfolio_sensitive,
        "negated": False,
        "counterfactual": False,
        "side_effects_allowed": False,
    }


def _trimatch_tiebreaker_considered_payload(
    *,
    active_trimatch: object | None,
    extra_tiebreaker: object,
    ensemble_intent: IntentVote,
    final_intent: IntentVote,
) -> dict[str, Any]:
    active_snapshot = _trimatch_snapshot(active_trimatch)
    extra_snapshot = _trimatch_snapshot(extra_tiebreaker)
    ensemble_snapshot = _intent_snapshot(ensemble_intent)
    final_snapshot = _intent_snapshot(final_intent)
    safety = _tiebreaker_safety_snapshot(
        extra_snapshot=extra_snapshot,
        final_snapshot=final_snapshot,
    )
    decision = _tiebreaker_candidate_decision(
        active_snapshot=active_snapshot,
        extra_snapshot=extra_snapshot,
        ensemble_snapshot=ensemble_snapshot,
        final_snapshot=final_snapshot,
        safety=safety,
    )

    return {
        "extra_tiebreaker": extra_snapshot,
        "before": {
            "active_trimatch": active_snapshot,
            "ensemble": ensemble_snapshot,
            "final_before_tiebreaker": final_snapshot,
        },
        "decision": decision,
        "safety": safety,
    }


def _apply_tiebreaker_to_intent(
    *,
    intent: IntentVote,
    decision: dict[str, Any],
) -> IntentVote:
    if decision.get("applied") is not True:
        return intent

    dimension = decision.get("dimension")
    recommended_value = decision.get("recommended_value")
    if not isinstance(dimension, str) or not isinstance(recommended_value, str):
        return intent

    updates: dict[str, Any] = {}
    try:
        if dimension == "query_primary":
            updates["query_primary"] = QueryIntentType(recommended_value)
        elif dimension == "service_primary":
            updates["service_primary"] = ServiceCategory(recommended_value)
        else:
            return intent
    except ValueError:
        return intent

    evidence = [
        *intent.evidence,
        f"trimatch tiebreaker applied {dimension}={recommended_value}",
    ]
    rationale = f"{intent.rationale} Tiebreaker applied safely for {dimension}={recommended_value}."

    return intent.model_copy(
        update={
            **updates,
            "evidence": evidence,
            "rationale": rationale,
        }
    )


def _tiebreaker_candidate_decision(
    *,
    active_snapshot: dict[str, Any] | None,
    extra_snapshot: dict[str, Any] | None,
    ensemble_snapshot: dict[str, Any],
    final_snapshot: dict[str, Any],
    safety: dict[str, Any],
) -> dict[str, Any]:
    dimension, recommended_value = _tiebreaker_recommendation(extra_snapshot)

    blocked_reasons = _tiebreaker_blocked_reasons(
        active_snapshot=active_snapshot,
        ensemble_snapshot=ensemble_snapshot,
        final_snapshot=final_snapshot,
        safety=safety,
        dimension=dimension,
        recommended_value=recommended_value,
    )
    eligible = not blocked_reasons

    return {
        "eligible": eligible,
        "applied": eligible,
        "dimension": dimension if eligible else None,
        "recommended_value": recommended_value if eligible else None,
        "reason": (
            "applied: safe tiebreaker resolved eligible intent disagreement"
            if eligible
            else "blocked: " + "; ".join(blocked_reasons)
        ),
        "blocked_reasons": blocked_reasons,
    }


def _tiebreaker_recommendation(
    extra_snapshot: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    if extra_snapshot is None:
        return None, None

    for dimension in ("query_primary", "service_primary"):
        value = extra_snapshot.get(dimension)
        if isinstance(value, str) and value:
            return dimension, value

    return None, None


def _tiebreaker_blocked_reasons(
    *,
    active_snapshot: dict[str, Any] | None,
    ensemble_snapshot: dict[str, Any],
    final_snapshot: dict[str, Any],
    safety: dict[str, Any],
    dimension: str | None,
    recommended_value: str | None,
) -> list[str]:
    blocked: list[str] = []

    if dimension is None or recommended_value is None:
        blocked.append("no extra tiebreaker recommendation")
        return blocked

    if dimension not in {"query_primary", "service_primary"}:
        blocked.append(f"unsupported dimension: {dimension}")

    if recommended_value == final_snapshot.get(dimension):
        blocked.append("recommendation already matches final intent")

    if not _has_qualifying_disagreement(
        active_snapshot=active_snapshot,
        ensemble_snapshot=ensemble_snapshot,
        final_snapshot=final_snapshot,
        dimension=dimension,
        recommended_value=recommended_value,
    ):
        blocked.append("no qualifying extra/final disagreement")

    extra_confidence = _float_or_zero(
        active_snapshot.get("confidence") if active_snapshot else None
    )
    if extra_confidence and extra_confidence < 0.0:
        blocked.append("invalid active confidence")

    if _float_or_zero(final_snapshot.get("confidence")) > 0.72:
        blocked.append("final confidence above tiebreaker threshold")

    if _sensitive_safety_blocked(safety):
        blocked.append("safety-sensitive intent cannot use tiebreaker")

    if recommended_value in {
        "pricing_question",
        "timeline_question",
        "portfolio_request",
        "nda_request",
        "agreement_request",
        "payment_question",
        "complaint_or_objection",
        "ready_to_buy",
        "spam_or_abuse",
        "off_topic",
    }:
        blocked.append(f"forbidden recommended value: {recommended_value}")

    return blocked


def _has_qualifying_disagreement(
    *,
    active_snapshot: dict[str, Any] | None,
    ensemble_snapshot: dict[str, Any],
    final_snapshot: dict[str, Any],
    dimension: str,
    recommended_value: str,
) -> bool:
    comparison_values = {
        active_snapshot.get(dimension) if active_snapshot else None,
        ensemble_snapshot.get(dimension),
        final_snapshot.get(dimension),
    }
    non_empty_values = {value for value in comparison_values if value is not None}
    return bool(non_empty_values) and any(value != recommended_value for value in non_empty_values)


def _sensitive_safety_blocked(safety: dict[str, Any]) -> bool:
    return any(
        bool(safety.get(key))
        for key in (
            "pricing_sensitive",
            "document_sensitive",
            "portfolio_sensitive",
            "negated",
            "counterfactual",
            "side_effects_allowed",
        )
    )


def _float_or_zero(value: object) -> float:
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _tiebreaker_safety_snapshot(
    *,
    extra_snapshot: dict[str, Any] | None,
    final_snapshot: dict[str, Any],
) -> dict[str, Any]:
    extra_query = extra_snapshot.get("query_primary") if extra_snapshot else None
    final_query = final_snapshot.get("query_primary")

    pricing_sensitive = extra_query in {
        "pricing_question",
        "timeline_question",
        "payment_question",
    } or final_query in {
        "pricing_question",
        "timeline_question",
        "payment_question",
    }
    document_sensitive = extra_query in {
        "nda_request",
        "agreement_request",
    } or final_query in {
        "nda_request",
        "agreement_request",
    }
    portfolio_sensitive = extra_query == "portfolio_request" or final_query == "portfolio_request"

    return {
        "pricing_sensitive": pricing_sensitive,
        "document_sensitive": document_sensitive,
        "portfolio_sensitive": portfolio_sensitive,
        "negated": False,
        "counterfactual": False,
        "side_effects_allowed": False,
    }


def _trimatch_advisory_payload(
    *,
    extra_advisory: object,
    final_intent: IntentVote,
) -> dict[str, Any]:
    extra_snapshot = _trimatch_snapshot(extra_advisory)
    final_snapshot = _intent_snapshot(final_intent)

    return {
        "extra_advisory": extra_snapshot,
        "final": final_snapshot,
        "recommendation": _advisory_recommendation(
            extra_snapshot=extra_snapshot,
            final_snapshot=final_snapshot,
        ),
        "advisory_applied": False,
        "side_effects_allowed": False,
    }


def _advisory_recommendation(
    *,
    extra_snapshot: dict[str, Any] | None,
    final_snapshot: dict[str, Any],
) -> dict[str, Any]:
    if extra_snapshot is None:
        return {
            "dimension": None,
            "recommended_value": None,
            "matches_final": False,
            "reason": "extra advisory RulePack produced no usable snapshot",
        }

    for dimension in ("query_primary", "service_primary", "funnel_stage"):
        recommended_value = extra_snapshot.get(dimension)
        if recommended_value is None:
            continue

        matches_final = recommended_value == final_snapshot.get(dimension)
        return {
            "dimension": dimension,
            "recommended_value": recommended_value,
            "matches_final": matches_final,
            "reason": (
                "extra advisory RulePack agreed with final intent"
                if matches_final
                else "extra advisory RulePack differed from final intent"
            ),
        }

    return {
        "dimension": None,
        "recommended_value": None,
        "matches_final": False,
        "reason": "extra advisory RulePack produced no recommendation",
    }


def _trimatch_disagreement_payload(
    *,
    active_trimatch: object | None,
    extra_shadow: object | None,
    ensemble_intent: IntentVote,
    final_intent: IntentVote,
) -> dict[str, Any]:
    final_snapshot = _intent_snapshot(final_intent)
    snapshots: dict[str, dict[str, Any] | None] = {
        "active_trimatch": _trimatch_snapshot(active_trimatch),
        "extra_shadow": _trimatch_snapshot(extra_shadow),
        "ensemble": _intent_snapshot(ensemble_intent),
    }

    disagreements: list[dict[str, Any]] = []
    for source, snapshot in snapshots.items():
        if snapshot is None:
            continue
        for dimension in ("query_primary", "service_primary", "funnel_stage"):
            source_value = snapshot.get(dimension)
            final_value = final_snapshot.get(dimension)
            if source_value is None:
                continue
            if source_value != final_value:
                disagreements.append(
                    {
                        "source": source,
                        "dimension": dimension,
                        "source_value": source_value,
                        "final_value": final_value,
                    }
                )

    extra_shadow_snapshot = snapshots["extra_shadow"]
    should_log = bool(disagreements)
    if (
        extra_shadow_snapshot is not None
        and int(extra_shadow_snapshot.get("evidence_count") or 0) > 0
    ):
        should_log = True

    return {
        "active_trimatch": snapshots["active_trimatch"],
        "extra_shadow": extra_shadow_snapshot,
        "ensemble": snapshots["ensemble"],
        "final": final_snapshot,
        "disagreements": disagreements,
        "should_log": should_log,
    }


def _trimatch_snapshot(result: object | None) -> dict[str, Any] | None:
    if result is None:
        return None

    return {
        "query_primary": _enum_value(getattr(result, "query_primary", None)),
        "service_primary": _enum_value(getattr(result, "service_primary", None)),
        "funnel_stage": _enum_value(getattr(result, "funnel_stage", None)),
        "confidence": getattr(result, "confidence", None),
        "evidence_count": len(getattr(result, "evidence", []) or []),
        "shortcut_eligible": bool(getattr(result, "shortcut_eligible", False)),
    }


def _intent_snapshot(intent: IntentVote) -> dict[str, Any]:
    return {
        "query_primary": _enum_value(intent.query_primary),
        "service_primary": _enum_value(intent.service_primary),
        "funnel_stage": _enum_value(intent.funnel_stage),
        "confidence": intent.confidence,
        "evidence_count": len(intent.evidence),
        "needs_clarification": intent.needs_clarification,
    }


def _enum_value(value: object | None) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", None)
    return str(enum_value if enum_value is not None else value)
