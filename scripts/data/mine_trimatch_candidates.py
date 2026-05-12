from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LAYER_TO_CANDIDATE_TYPE = {
    "exact": "exact_phrase",
    "regex": "regex",
    "pattern": "pattern",
    "semantic": "semantic_cluster",
    "fuzzy": "fuzzy_variant",
    "context": "service_priority_rule",
}

SUPPORTED_DIMENSIONS = {"service_intent", "query_intent", "funnel_stage"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mine human-review Tri-Match candidate rules from reports."
    )
    parser.add_argument(
        "--shadow-eval-report",
        default="reports/trimatch/rules_army_v2_shadow_eval.json",
    )
    parser.add_argument(
        "--production-flow-report",
        default=None,
        help="Optional production-flow JSON report. Defaults to latest if present.",
    )
    parser.add_argument(
        "--output",
        default="data/trimatch/reinforcement/candidates/generated/candidates.auto.jsonl",
    )
    parser.add_argument("--max-candidates", type=int, default=50)
    args = parser.parse_args()

    candidates = _mine_candidates(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("w", encoding="utf-8") as handle:
        for candidate in candidates:
            handle.write(json.dumps(candidate, sort_keys=True) + "\n")

    result = {
        "valid": True,
        "output": str(output),
        "candidate_count": len(candidates),
        "source_shadow_eval": args.shadow_eval_report,
        "source_production_flow": args.production_flow_report or "latest_if_present",
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _mine_candidates(args: argparse.Namespace) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    generated_at = now.isoformat().replace("+00:00", "Z")
    date_token = now.strftime("%Y%m%d")

    raw_candidates: list[dict[str, Any]] = []

    shadow_path = Path(args.shadow_eval_report)
    if shadow_path.exists():
        raw_candidates.extend(
            _mine_shadow_eval(
                shadow_path=shadow_path,
                generated_at=generated_at,
                date_token=date_token,
            )
        )

    production_path = _resolve_production_flow_report(args.production_flow_report)
    if production_path is not None:
        raw_candidates.extend(
            _mine_production_flow(
                production_path=production_path,
                generated_at=generated_at,
                date_token=date_token,
            )
        )

    unique = _dedupe_candidates(raw_candidates)
    limited = unique[: args.max_candidates]

    for index, candidate in enumerate(limited, start=9001):
        candidate["candidate_id"] = f"cand_{date_token}_{index}"

    return limited


def _mine_shadow_eval(
    shadow_path: Path,
    generated_at: str,
    date_token: str,
) -> list[dict[str, Any]]:
    del date_token

    report = json.loads(shadow_path.read_text(encoding="utf-8"))
    improvements = report.get("improvements", [])

    candidates: list[dict[str, Any]] = []

    for row in improvements:
        dimension = str(row.get("dimension", ""))
        expected = str(row.get("expected", ""))
        if dimension not in SUPPORTED_DIMENSIONS or not expected:
            continue

        staged = row.get("staged", {})
        evidence = staged.get("top_evidence", [])
        top = evidence[0] if evidence else {}

        layer = str(top.get("layer") or "pattern")
        candidate_type = LAYER_TO_CANDIDATE_TYPE.get(layer, "pattern")
        matched_text = str(top.get("matched_text") or "").strip()
        text = str(row.get("text") or "").strip()
        proposal_value = matched_text or _fallback_phrase(text)

        if not proposal_value:
            continue

        candidates.append(
            {
                "candidate_id": "cand_19700101_0000",
                "candidate_type": candidate_type,
                "target_layer": layer if layer in LAYER_TO_CANDIDATE_TYPE else "pattern",
                "target_dimension": dimension,
                "target_label": expected,
                "source": {
                    "source_type": "shadow_eval",
                    "source_id": f"rules_army_v2_shadow_eval_row_{row.get('index')}",
                    "conversation_id": None,
                    "turn_index": int(row.get("index") or 1),
                },
                "proposal": {
                    "value": proposal_value,
                    "normalized_value": proposal_value.lower(),
                    "regex_flags": "i" if candidate_type == "regex" else None,
                },
                "positive_examples": _positive_examples(text, expected),
                "negative_examples": _negative_examples(proposal_value, expected),
                "risk_note": _risk_note(dimension, expected),
                "suggested_weight": _confidence(staged.get("confidence")),
                "suggested_by": "tri_match_shadow",
                "status": "pending_human_review",
                "created_at": generated_at,
            }
        )

    return candidates


def _mine_production_flow(
    production_path: Path,
    generated_at: str,
    date_token: str,
) -> list[dict[str, Any]]:
    del date_token

    report = json.loads(production_path.read_text(encoding="utf-8"))
    turns = report.get("turns", [])
    candidates: list[dict[str, Any]] = []

    for turn in turns:
        if turn.get("passed") is True:
            continue

        diagnostics = turn.get("diagnostics", {})
        expected_findings = diagnostics.get("expected_findings", [])
        text = _turn_message(turn)

        if not text:
            continue

        inferred_dimension, inferred_label = _infer_target_from_findings(expected_findings)
        if inferred_dimension is None or inferred_label is None:
            continue

        candidates.append(
            {
                "candidate_id": "cand_19700101_0000",
                "candidate_type": "pattern",
                "target_layer": "pattern",
                "target_dimension": inferred_dimension,
                "target_label": inferred_label,
                "source": {
                    "source_type": "diagnostic_failure",
                    "source_id": str(turn.get("name") or f"turn_{turn.get('index')}"),
                    "conversation_id": None,
                    "turn_index": int(turn.get("index") or 1),
                },
                "proposal": {
                    "value": _fallback_phrase(text),
                    "normalized_value": _fallback_phrase(text).lower(),
                    "regex_flags": None,
                },
                "positive_examples": _positive_examples(text, inferred_label),
                "negative_examples": _negative_examples(text, inferred_label),
                "risk_note": _risk_note(inferred_dimension, inferred_label),
                "suggested_weight": 0.6,
                "suggested_by": "diagnostic_analyzer",
                "status": "pending_human_review",
                "created_at": generated_at,
            }
        )

    return candidates


def _resolve_production_flow_report(path_value: str | None) -> Path | None:
    if path_value:
        path = Path(path_value)
        return path if path.exists() else None

    reports_dir = Path("reports/production-flow")
    if not reports_dir.exists():
        return None

    reports = sorted(reports_dir.glob("production_flow_50_*.json"))
    return reports[-1] if reports else None


def _turn_message(turn: dict[str, Any]) -> str:
    for key in ("message", "input", "text", "user_message"):
        value = turn.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(turn.get("name") or "").replace("_", " ")


def _infer_target_from_findings(findings: list[object]) -> tuple[str | None, str | None]:
    joined = " ".join(str(item) for item in findings)

    if "unexpected_service" in joined:
        return "service_intent", "service_question"
    if "unexpected_intent" in joined:
        return "query_intent", "service_question"
    if "unexpected_funnel" in joined:
        return "funnel_stage", "service_discovery"

    return None, None


def _positive_examples(text: str, label: str) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        cleaned = f"I need help with {label.replace('_', ' ')}."

    return [
        cleaned,
        f"Please help me with {label.replace('_', ' ')}.",
        f"I want BookCraft to review this as {label.replace('_', ' ')}.",
    ]


def _negative_examples(proposal_value: str, label: str) -> list[str]:
    readable_label = label.replace("_", " ")
    return [
        f"I do not need {proposal_value}.",
        f"Do not include {readable_label} in this scope.",
        f"{readable_label.title()} is not approved yet.",
    ]


def _risk_note(dimension: str, label: str) -> str:
    return (
        f"Candidate targets {dimension}:{label}; reviewer must confirm it does not "
        "overmatch negated, hedged, counterfactual, pricing-sensitive, or agreement "
        "readiness contexts."
    )


def _confidence(value: object) -> float:
    if isinstance(value, int | float):
        return max(0.0, min(1.0, float(value)))
    return 0.7


def _fallback_phrase(text: str) -> str:
    words = text.strip().split()
    return " ".join(words[:8])


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[dict[str, Any]] = []

    for candidate in candidates:
        key = (
            str(candidate.get("target_dimension")),
            str(candidate.get("target_label")),
            str(candidate.get("target_layer")),
            str(candidate.get("proposal", {}).get("value", "")).lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)

    return unique


if __name__ == "__main__":
    raise SystemExit(main())
