from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bookcraft.components.trimatch.schemas import RulePack

APPROVED_DECISIONS = {"approve", "edit_and_approve"}
DEFAULT_OUTPUT = "data/trimatch/reinforcement/staged_from_reviews/approved_candidates.rulepack.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile human-approved Tri-Match candidates into a staged RulePack."
    )
    parser.add_argument(
        "--reinforcement-root",
        default="data/trimatch/reinforcement",
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--version",
        default=None,
        help="Optional RulePack version. Defaults to timestamped version.",
    )
    args = parser.parse_args()

    root = Path(args.reinforcement_root)
    output = Path(args.output)
    version = args.version or _version()

    candidates = _load_jsonl_many(root / "candidates")
    reviews = _load_jsonl_many(root / "reviews")
    rule_pack = _compile_rule_pack(candidates, reviews, version)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rule_pack, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # Validate against the production RulePack schema.
    validated = RulePack.model_validate(rule_pack)

    result = {
        "valid": True,
        "output": str(output),
        "version": validated.version,
        "compiled_rule_count": len(validated.rules),
        "approved_review_count": sum(
            1 for review in reviews if review.get("decision") in APPROVED_DECISIONS
        ),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _compile_rule_pack(
    candidates: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    version: str,
) -> dict[str, Any]:
    candidate_by_id = {
        str(candidate.get("candidate_id")): candidate
        for candidate in candidates
        if candidate.get("candidate_id")
    }

    rules: list[dict[str, Any]] = []
    seen_rule_ids: set[str] = set()

    for review in reviews:
        if review.get("decision") not in APPROVED_DECISIONS:
            continue

        candidate_id = str(review.get("candidate_id") or "")
        candidate = candidate_by_id.get(candidate_id)
        if candidate is None:
            continue

        rule = _candidate_to_rule(candidate, review)
        if rule["id"] in seen_rule_ids:
            continue

        seen_rule_ids.add(str(rule["id"]))
        rules.append(rule)

    return {
        "version": version,
        "rules": rules,
    }


def _candidate_to_rule(candidate: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    layer = _normalize_layer(candidate)
    dimension = str(candidate.get("target_dimension") or "")
    label = str(candidate.get("target_label") or "")
    proposal = _proposal_value(candidate, review)
    confidence = _confidence(candidate.get("suggested_weight"))

    rule: dict[str, Any] = {
        "id": _rule_id(candidate),
        "layer": layer,
        "target": _target(dimension, label),
        "confidence": confidence,
        "enabled": True,
        # Review-approved rules still must prove themselves before shortcut.
        "shortcut_allowed": False,
        "phrases": [],
        "regex": None,
        "pattern": [],
        "semantic_examples": [],
    }

    if layer == "exact":
        rule["phrases"] = [proposal]
    elif layer == "regex":
        rule["regex"] = proposal
    elif layer == "pattern":
        rule["pattern"] = [proposal]
    elif layer == "semantic":
        rule["semantic_examples"] = _semantic_examples(candidate, proposal)
    elif layer == "fuzzy":
        # Current RulePack enum supports fuzzy but matcher payload has no fuzzy field.
        # Stage as pattern to keep the rule pack valid until fuzzy runtime is wired.
        rule["layer"] = "pattern"
        rule["pattern"] = [proposal]
    else:
        rule["layer"] = "pattern"
        rule["pattern"] = [proposal]

    return rule


def _normalize_layer(candidate: dict[str, Any]) -> str:
    layer = str(candidate.get("target_layer") or "pattern")
    if layer == "context":
        return "pattern"
    return layer


def _target(dimension: str, label: str) -> dict[str, Any]:
    if dimension == "service_intent":
        return {
            "service_intent": label,
            "query_intent": None,
            "funnel_stage": None,
        }
    if dimension == "query_intent":
        return {
            "service_intent": None,
            "query_intent": label,
            "funnel_stage": None,
        }
    if dimension == "funnel_stage":
        return {
            "service_intent": None,
            "query_intent": None,
            "funnel_stage": label,
        }

    raise ValueError(f"unsupported target_dimension: {dimension}")


def _proposal_value(candidate: dict[str, Any], review: dict[str, Any]) -> str:
    edited = review.get("edited_proposal")
    if isinstance(edited, dict) and isinstance(edited.get("value"), str):
        return edited["value"]

    proposal = candidate.get("proposal")
    if isinstance(proposal, dict) and isinstance(proposal.get("value"), str):
        return proposal["value"]

    raise ValueError(f"candidate has no proposal value: {candidate.get('candidate_id')}")


def _semantic_examples(candidate: dict[str, Any], proposal: str) -> list[str]:
    examples = [proposal]
    positives = candidate.get("positive_examples")
    if isinstance(positives, list):
        for item in positives:
            if isinstance(item, str) and item not in examples:
                examples.append(item)
    return examples[:5]


def _confidence(value: object) -> float:
    if isinstance(value, int | float):
        return round(max(0.0, min(1.0, float(value))), 4)
    return 0.7


def _rule_id(candidate: dict[str, Any]) -> str:
    candidate_id = str(candidate.get("candidate_id") or "unknown")
    safe = re.sub(r"[^a-zA-Z0-9_]+", "_", candidate_id)
    return f"reviewed_{safe}"


def _load_jsonl_many(directory: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not directory.exists():
        return rows

    for path in sorted(directory.rglob("*.jsonl")):
        rows.extend(_load_jsonl(path))

    return rows


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        loaded = json.loads(line)
        if isinstance(loaded, dict):
            rows.append(loaded)
    return rows


def _version() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"approved_candidates.{stamp}"


if __name__ == "__main__":
    raise SystemExit(main())
