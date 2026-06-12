#!/usr/bin/env python3
"""Beta-posterior confidence calibration for TriMatch rules.

Usage:
    python scripts/data/calibrate_trimatch_confidence.py \\
        --rule-dir data/trimatch/rules \\
        --eval-file data/trimatch/eval/service_intent_eval.v1.jsonl \\
        --output data/trimatch/calibration/confidence_patch.json

The eval JSONL format expected:
    {"rule_id": "SERVICE-GW-EX-001", "correct": true, "text": "...", "predicted": "ghostwriting"}
or:
    {"rule_ids_matched": ["SERVICE-GW-EX-001"], "correct": true, "text": "..."}

Output JSON format:
    {
        "version": "calibrated-2024-01-01",
        "patches": [
            {"rule_id": "SERVICE-GW-EX-001", "old_confidence": 0.85, "new_confidence": 0.91, "n_correct": 45, "n_total": 50}
        ]
    }
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path


def load_rules(rule_dir: Path) -> dict[str, dict]:
    """Load all rules from rule pack JSON files. Returns {rule_id: rule_dict}."""
    rules: dict[str, dict] = {}
    for path in sorted(rule_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for rule in data.get("rules", []):
                if rule.get("id"):
                    rules[rule["id"]] = rule
        except Exception as e:
            print(f"Warning: could not load {path}: {e}", file=sys.stderr)
    return rules


def load_eval_outcomes(eval_file: Path) -> dict[str, tuple[int, int]]:
    """Load evaluation outcomes. Returns {rule_id: (n_correct, n_total)}."""
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])

    with eval_file.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            is_correct = bool(entry.get("correct", entry.get("expected_match", False)))

            # Support multiple formats
            rule_ids: list[str] = []
            if "rule_id" in entry:
                rule_ids = [entry["rule_id"]]
            elif "rule_ids_matched" in entry:
                rule_ids = list(entry["rule_ids_matched"])
            elif "matched_rule_id" in entry:
                rule_ids = [entry["matched_rule_id"]]

            for rid in rule_ids:
                counts[rid][1] += 1  # total
                if is_correct:
                    counts[rid][0] += 1  # correct

    return {rid: (c[0], c[1]) for rid, c in counts.items()}


def beta_posterior_confidence(n_correct: int, n_total: int) -> float:
    """Compute beta posterior mean confidence with Laplace smoothing.

    Beta(alpha=n_correct+1, beta=n_incorrect+1)
    Mean = alpha / (alpha + beta) = (n_correct + 1) / (n_total + 2)
    """
    return (n_correct + 1) / (n_total + 2)


def calibrate(
    rule_dir: Path,
    eval_file: Path,
    output_path: Path,
    min_observations: int = 5,
    max_delta: float = 0.3,
) -> None:
    """Run calibration and write output patch file."""
    rules = load_rules(rule_dir)
    outcomes = load_eval_outcomes(eval_file)

    patches = []
    for rule_id, (n_correct, n_total) in outcomes.items():
        if n_total < min_observations:
            print(f"Skipping {rule_id}: only {n_total} observations (min={min_observations})", file=sys.stderr)
            continue

        rule = rules.get(rule_id)
        if rule is None:
            print(f"Warning: rule {rule_id} in eval but not in rule pack", file=sys.stderr)
            continue

        old_confidence = float(rule.get("confidence", 0.85))
        new_confidence = beta_posterior_confidence(n_correct, n_total)

        # Clamp change to max_delta to avoid large sudden swings
        if abs(new_confidence - old_confidence) > max_delta:
            if new_confidence > old_confidence:
                new_confidence = old_confidence + max_delta
            else:
                new_confidence = old_confidence - max_delta
        new_confidence = round(max(0.0, min(1.0, new_confidence)), 4)

        patches.append({
            "rule_id": rule_id,
            "old_confidence": old_confidence,
            "new_confidence": new_confidence,
            "n_correct": n_correct,
            "n_total": n_total,
            "accuracy": round(n_correct / n_total, 4) if n_total > 0 else 0.0,
        })

    patches.sort(key=lambda p: abs(p["new_confidence"] - p["old_confidence"]), reverse=True)

    output = {
        "version": f"calibrated-{date.today().isoformat()}",
        "source_eval": str(eval_file),
        "source_rules": str(rule_dir),
        "patches": patches,
        "summary": {
            "total_rules_evaluated": len(outcomes),
            "patches_generated": len(patches),
            "skipped_low_observations": len(outcomes) - len(patches),
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Written {len(patches)} patches to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate TriMatch rule confidence using beta posteriors.")
    parser.add_argument("--rule-dir", type=Path, default=Path("data/trimatch/rules"), help="Rule pack directory")
    parser.add_argument("--eval-file", type=Path, required=True, help="Evaluation JSONL file")
    parser.add_argument("--output", type=Path, default=Path("data/trimatch/calibration/confidence_patch.json"), help="Output patch file")
    parser.add_argument("--min-observations", type=int, default=5, help="Minimum observations per rule")
    parser.add_argument("--max-delta", type=float, default=0.3, help="Maximum allowed confidence change per calibration run")

    args = parser.parse_args()

    if not args.eval_file.exists():
        print(f"Error: eval file {args.eval_file} not found", file=sys.stderr)
        sys.exit(1)

    calibrate(
        rule_dir=args.rule_dir,
        eval_file=args.eval_file,
        output_path=args.output,
        min_observations=args.min_observations,
        max_delta=args.max_delta,
    )


if __name__ == "__main__":
    main()
