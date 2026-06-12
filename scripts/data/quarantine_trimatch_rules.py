#!/usr/bin/env python3
"""Demotion and quarantine script for underperforming TriMatch rules.

Usage:
    python scripts/data/quarantine_trimatch_rules.py \\
        --rule-dir data/trimatch/rules \\
        --eval-file data/trimatch/eval/service_intent_eval.v1.jsonl \\
        --output data/trimatch/quarantine/quarantine_list.json \\
        --threshold 0.5

A rule is quarantined if its accuracy < threshold AND it has >= min-observations.
A quarantined rule's JSON entry gets "enabled": false applied in a patch.

Output format:
    {
        "quarantine_list": [
            {"rule_id": "BAD-RULE-001", "accuracy": 0.32, "n_total": 20, "reason": "accuracy_below_threshold"}
        ],
        "patch_file": "data/trimatch/quarantine/disabled_rules.json"
    }
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path


def load_eval_outcomes(eval_file: Path) -> dict[str, tuple[int, int]]:
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
            rule_ids: list[str] = []
            if "rule_id" in entry:
                rule_ids = [entry["rule_id"]]
            elif "rule_ids_matched" in entry:
                rule_ids = list(entry["rule_ids_matched"])
            elif "matched_rule_id" in entry:
                rule_ids = [entry["matched_rule_id"]]

            for rid in rule_ids:
                counts[rid][1] += 1
                if is_correct:
                    counts[rid][0] += 1

    return {rid: (c[0], c[1]) for rid, c in counts.items()}


def quarantine(
    rule_dir: Path,
    eval_file: Path,
    output_path: Path,
    threshold: float = 0.5,
    min_observations: int = 5,
    dry_run: bool = False,
) -> None:
    outcomes = load_eval_outcomes(eval_file)

    quarantine_list = []
    for rule_id, (n_correct, n_total) in outcomes.items():
        if n_total < min_observations:
            continue
        accuracy = n_correct / n_total
        if accuracy < threshold:
            quarantine_list.append({
                "rule_id": rule_id,
                "accuracy": round(accuracy, 4),
                "n_correct": n_correct,
                "n_total": n_total,
                "reason": "accuracy_below_threshold",
            })

    quarantine_list.sort(key=lambda r: r["accuracy"])

    # Write quarantine list
    output_path.parent.mkdir(parents=True, exist_ok=True)

    disabled_patch_path = output_path.parent / "disabled_rules.json"
    disabled_patch = {
        "version": f"quarantine-{date.today().isoformat()}",
        "disabled_rule_ids": [r["rule_id"] for r in quarantine_list],
        "patches": [{"rule_id": r["rule_id"], "set": {"enabled": False}} for r in quarantine_list],
    }

    result = {
        "quarantine_date": date.today().isoformat(),
        "threshold": threshold,
        "min_observations": min_observations,
        "quarantine_count": len(quarantine_list),
        "quarantine_list": quarantine_list,
        "patch_file": str(disabled_patch_path),
        "dry_run": dry_run,
    }

    if not dry_run:
        output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        disabled_patch_path.write_text(json.dumps(disabled_patch, indent=2), encoding="utf-8")
        print(f"Quarantined {len(quarantine_list)} rules -> {output_path}")
        print(f"Disable patch written -> {disabled_patch_path}")
    else:
        print(f"[DRY RUN] Would quarantine {len(quarantine_list)} rules:")
        for r in quarantine_list:
            print(f"  {r['rule_id']}: accuracy={r['accuracy']:.1%} ({r['n_correct']}/{r['n_total']})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Quarantine underperforming TriMatch rules.")
    parser.add_argument("--rule-dir", type=Path, default=Path("data/trimatch/rules"))
    parser.add_argument("--eval-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("data/trimatch/quarantine/quarantine_list.json"))
    parser.add_argument("--threshold", type=float, default=0.5, help="Accuracy threshold below which rules are quarantined")
    parser.add_argument("--min-observations", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true", help="Print what would be quarantined without writing files")

    args = parser.parse_args()

    if not args.eval_file.exists():
        print(f"Error: eval file {args.eval_file} not found", file=sys.stderr)
        sys.exit(1)

    quarantine(
        rule_dir=args.rule_dir,
        eval_file=args.eval_file,
        output_path=args.output,
        threshold=args.threshold,
        min_observations=args.min_observations,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
