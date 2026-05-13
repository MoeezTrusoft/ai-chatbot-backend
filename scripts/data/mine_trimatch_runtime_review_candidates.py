from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SUPPORTED_FINDING_TARGETS = {
    "unexpected_final_query": ("query_intent", "pattern", "pattern"),
    "unexpected_final_service": ("service_intent", "pattern", "pattern"),
    "unexpected_shadow_service": ("service_intent", "pattern", "pattern"),
}

DISAGREEMENT_SOURCE_TYPE = "llm_disagreement"
DIAGNOSTIC_SOURCE_TYPE = "diagnostic_failure"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Mine human-review Tri-Match candidates from the shadow runtime review report."
        )
    )
    parser.add_argument(
        "--runtime-review-report",
        default="reports/trimatch/trimatch_shadow_runtime_review.json",
    )
    parser.add_argument(
        "--output",
        default=(
            "data/trimatch/reinforcement/candidates/generated/runtime_review_candidates.auto.jsonl"
        ),
    )
    parser.add_argument(
        "--id-start",
        type=int,
        default=9701,
        help="Four-digit starting suffix for generated candidate IDs.",
    )
    parser.add_argument(
        "--include-passed-disagreements",
        action="store_true",
        help=(
            "Also mine useful shadow/final disagreements from passed turns. "
            "Synthetic marker cases are always skipped."
        ),
    )
    args = parser.parse_args()

    candidates = _mine_candidates(args)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for candidate in candidates:
            handle.write(json.dumps(candidate, sort_keys=True) + "\n")

    result = {
        "valid": True,
        "source_report": args.runtime_review_report,
        "output": str(output),
        "candidate_count": len(candidates),
        "note": (
            "Candidates are pending human review only. This script does not "
            "activate or compile any runtime rules."
        ),
    }

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _mine_candidates(args: argparse.Namespace) -> list[dict[str, Any]]:
    report_path = Path(args.runtime_review_report)
    if not report_path.exists():
        raise SystemExit(f"missing runtime review report: {report_path}")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    turns = report.get("turns", [])
    if not isinstance(turns, list):
        raise SystemExit("runtime review report has invalid turns payload")

    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    date_token = datetime.now(UTC).strftime("%Y%m%d")

    raw_candidates: list[dict[str, Any]] = []

    for turn in turns:
        if not isinstance(turn, dict):
            continue
        raw_candidates.extend(
            _mine_failed_turn_candidates(
                turn=turn,
                generated_at=generated_at,
            )
        )
        if args.include_passed_disagreements:
            raw_candidates.extend(
                _mine_passed_disagreement_candidates(
                    turn=turn,
                    generated_at=generated_at,
                )
            )

    unique = _dedupe(raw_candidates)
    for offset, candidate in enumerate(unique):
        candidate["candidate_id"] = f"cand_{date_token}_{args.id_start + offset:04d}"

    return unique


def _mine_failed_turn_candidates(
    *,
    turn: dict[str, Any],
    generated_at: str,
) -> list[dict[str, Any]]:
    findings = turn.get("findings", [])
    if not isinstance(findings, list) or not findings:
        return []

    candidates: list[dict[str, Any]] = []

    for finding in findings:
        if not isinstance(finding, dict):
            continue

        finding_type = str(finding.get("type") or "")
        if finding_type in SUPPORTED_FINDING_TARGETS:
            dimension, target_layer, candidate_type = SUPPORTED_FINDING_TARGETS[finding_type]
            target_label = str(finding.get("expected") or "").strip()
            if not target_label:
                continue

            candidates.append(
                _candidate(
                    turn=turn,
                    finding_type=finding_type,
                    source_type=DIAGNOSTIC_SOURCE_TYPE,
                    target_dimension=dimension,
                    target_label=target_label,
                    target_layer=target_layer,
                    candidate_type=candidate_type,
                    suggested_by="diagnostic_analyzer",
                    suggested_weight=0.62,
                    generated_at=generated_at,
                )
            )

        elif finding_type == "disallowed_final_service_detected":
            actual = str(finding.get("actual") or "").strip()
            if not actual:
                continue

            candidates.append(
                _candidate(
                    turn=turn,
                    finding_type=finding_type,
                    source_type=DIAGNOSTIC_SOURCE_TYPE,
                    target_dimension="context",
                    target_label=f"negated_service_guard:{actual}",
                    target_layer="context",
                    candidate_type="negation_cue",
                    suggested_by="diagnostic_analyzer",
                    suggested_weight=0.78,
                    generated_at=generated_at,
                )
            )

    return candidates


def _mine_passed_disagreement_candidates(
    *,
    turn: dict[str, Any],
    generated_at: str,
) -> list[dict[str, Any]]:
    if not bool(turn.get("passed")):
        return []

    message = str(turn.get("message") or "")
    if _is_synthetic_marker_case(message):
        return []

    actual = turn.get("actual", {})
    if not isinstance(actual, dict):
        return []

    final_service = actual.get("final_service")
    shadow_service = actual.get("shadow_service")

    if not shadow_service or shadow_service == final_service:
        return []

    return [
        _candidate(
            turn=turn,
            finding_type="shadow_final_service_disagreement",
            source_type=DISAGREEMENT_SOURCE_TYPE,
            target_dimension="service_intent",
            target_label=str(shadow_service),
            target_layer="context",
            candidate_type="service_priority_rule",
            suggested_by="tri_match_shadow",
            suggested_weight=0.55,
            generated_at=generated_at,
        )
    ]


def _candidate(
    *,
    turn: dict[str, Any],
    finding_type: str,
    source_type: str,
    target_dimension: str,
    target_label: str,
    target_layer: str,
    candidate_type: str,
    suggested_by: str,
    suggested_weight: float,
    generated_at: str,
) -> dict[str, Any]:
    message = str(turn.get("message") or turn.get("name") or "").strip()
    name = str(turn.get("name") or "runtime_review_turn")
    index = int(turn.get("index") or 1)
    proposal = _proposal_from_message(message)

    return {
        "candidate_id": "cand_19700101_0000",
        "candidate_type": candidate_type,
        "target_layer": target_layer,
        "target_dimension": target_dimension,
        "target_label": target_label,
        "source": {
            "source_type": source_type,
            "source_id": f"trimatch_shadow_runtime_review:{name}:{finding_type}",
            "conversation_id": None,
            "turn_index": index,
        },
        "proposal": {
            "value": proposal,
            "normalized_value": proposal.lower(),
            "regex_flags": None,
        },
        "positive_examples": _positive_examples(
            message=message,
            label=target_label,
        ),
        "negative_examples": _negative_examples(
            proposal=proposal,
            label=target_label,
        ),
        "risk_note": _risk_note(
            finding_type=finding_type,
            target_dimension=target_dimension,
            target_label=target_label,
        ),
        "suggested_weight": suggested_weight,
        "suggested_by": suggested_by,
        "status": "pending_human_review",
        "created_at": generated_at,
    }


def _proposal_from_message(message: str) -> str:
    cleaned = " ".join(message.strip().split())
    if not cleaned:
        return "runtime review candidate"
    words = cleaned.split()
    return " ".join(words[:10])


def _positive_examples(*, message: str, label: str) -> list[str]:
    readable = label.replace("_", " ").replace(":", " ")
    base = message.strip() or f"I need help with {readable}."

    return [
        base,
        f"Please classify this BookCraft request as {readable}.",
        f"The customer is asking about {readable} in this BookCraft chat.",
    ]


def _negative_examples(*, proposal: str, label: str) -> list[str]:
    readable = label.replace("_", " ").replace(":", " ")

    return [
        f"I do not need {proposal}.",
        f"Do not treat this as {readable} when the user negates it.",
        f"{readable.title()} is not approved when the message is hypothetical only.",
    ]


def _risk_note(
    *,
    finding_type: str,
    target_dimension: str,
    target_label: str,
) -> str:
    return (
        f"Runtime-review candidate from {finding_type}; reviewer must confirm "
        f"{target_dimension}:{target_label} does not overmatch negated, hedged, "
        "counterfactual, pricing-sensitive, portfolio-sensitive, NDA, or "
        "agreement-readiness contexts."
    )


def _is_synthetic_marker_case(message: str) -> bool:
    lowered = message.casefold()
    return "rare shadow" in lowered or "shadow marker" in lowered


def _dedupe(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[dict[str, Any]] = []

    for candidate in candidates:
        proposal = candidate.get("proposal", {})
        if not isinstance(proposal, dict):
            continue

        key = (
            str(candidate.get("target_dimension")),
            str(candidate.get("target_label")),
            str(candidate.get("target_layer")),
            str(proposal.get("normalized_value") or proposal.get("value") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)

    return unique


if __name__ == "__main__":
    raise SystemExit(main())
