from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bookcraft.components.preprocessor.processor import SharedPreprocessor
from bookcraft.components.preprocessor.sidecars import load_sidecars
from bookcraft.components.trimatch.engine import TriMatchEngine
from bookcraft.components.trimatch.schemas import (
    EvalExample,
    RulePack,
    TriMatchDimension,
    TriMatchMode,
    TriMatchResult,
    TriMatchRule,
)


@dataclass(slots=True)
class StaticEmbeddingClient:
    dimensions: int = 384

    async def embed(self, normalized_text: str, language: str) -> list[float]:
        del normalized_text, language
        return [0.0] * self.dimensions


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Shadow-evaluate staged Tri-Match Rules Army v2 against active rules."
    )
    parser.add_argument("--active-rules-dir", default="data/trimatch/rules")
    parser.add_argument(
        "--staged-root",
        default="data/trimatch/staged/rules_army_v2",
    )
    parser.add_argument("--sidecars-dir", default="data/trimatch/sidecars")
    parser.add_argument("--output-dir", default="reports/trimatch")
    parser.add_argument("--fail-on-invalid", action="store_true")
    args = parser.parse_args()

    report = asyncio.run(_evaluate(args))

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "rules_army_v2_shadow_eval.json"
    md_path = output_dir / "rules_army_v2_shadow_eval.md"

    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")

    if args.fail_on_invalid and not report["summary"]["valid"]:
        return 1

    return 0


async def _evaluate(args: argparse.Namespace) -> dict[str, Any]:
    active_pack = _load_combined_rule_pack(
        Path(args.active_rules_dir),
        version="active_combined",
    )
    staged_root = Path(args.staged_root)
    staged_pack = _load_combined_rule_pack(
        staged_root / "rules",
        version="rules_army_v2_combined",
    )
    examples = _load_eval_examples(staged_root / "eval")

    sidecars = load_sidecars(args.sidecars_dir)
    preprocessor = SharedPreprocessor(
        sidecars=sidecars,
        embedding_client=StaticEmbeddingClient(),
    )

    active_engine = TriMatchEngine(
        rule_pack=active_pack,
        mode=TriMatchMode.SHADOW,
        fuzzy_enabled=False,
    )
    staged_engine = TriMatchEngine(
        rule_pack=staged_pack,
        mode=TriMatchMode.SHADOW,
        fuzzy_enabled=False,
    )

    rows: list[dict[str, Any]] = []

    for index, example in enumerate(examples, start=1):
        processed = await preprocessor.process(example.text)
        active_result = active_engine.classify(processed)
        staged_result = staged_engine.classify(processed)

        active_value = _value_for_dimension(active_result, example.dimension)
        staged_value = _value_for_dimension(staged_result, example.dimension)

        active_passed = active_value == example.expected
        staged_passed = staged_value == example.expected

        rows.append(
            {
                "index": index,
                "subset": example.subset,
                "dimension": example.dimension.value,
                "text": example.text,
                "expected": example.expected,
                "active": {
                    "value": active_value,
                    "passed": active_passed,
                    "confidence": active_result.confidence,
                    "evidence_count": len(active_result.evidence),
                    "top_evidence": _top_evidence(active_result),
                },
                "staged": {
                    "value": staged_value,
                    "passed": staged_passed,
                    "confidence": staged_result.confidence,
                    "evidence_count": len(staged_result.evidence),
                    "top_evidence": _top_evidence(staged_result),
                },
                "classification": _classify_comparison(active_passed, staged_passed),
            }
        )

    summary = _summary(rows, active_pack, staged_pack)
    high_risk = [
        row for row in rows if row["classification"] == "regression_active_correct_staged_wrong"
    ]
    improvements = [
        row for row in rows if row["classification"] == "improvement_active_wrong_staged_correct"
    ]
    disagreements = [row for row in rows if row["active"]["value"] != row["staged"]["value"]]

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "active_rules_dir": str(args.active_rules_dir),
        "staged_root": str(args.staged_root),
        "sidecars_dir": str(args.sidecars_dir),
        "summary": summary,
        "high_risk_regressions": high_risk[:100],
        "improvements": improvements[:100],
        "disagreements": disagreements[:150],
        "rows": rows,
    }


def _load_combined_rule_pack(directory: Path, version: str) -> RulePack:
    rules: list[TriMatchRule] = []
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        pack = RulePack.model_validate(data)
        rules.extend(pack.rules)

    return RulePack(version=version, rules=rules)


def _load_eval_examples(directory: Path) -> list[EvalExample]:
    examples: list[EvalExample] = []

    for path in sorted(directory.glob("*.jsonl")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for line_number, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                examples.append(EvalExample.model_validate(data))
            except Exception as exc:  # noqa: BLE001
                msg = f"Invalid eval example in {path}:{line_number}: {exc}"
                raise ValueError(msg) from exc

    if not examples:
        msg = f"No eval examples found in {directory}"
        raise ValueError(msg)

    return examples


def _value_for_dimension(result: TriMatchResult, dimension: TriMatchDimension) -> str | None:
    if dimension == TriMatchDimension.SERVICE_INTENT:
        return result.service_primary.value if result.service_primary is not None else None
    if dimension == TriMatchDimension.QUERY_INTENT:
        return result.query_primary.value if result.query_primary is not None else None
    if dimension == TriMatchDimension.FUNNEL_STAGE:
        return result.funnel_stage.value if result.funnel_stage is not None else None
    return None


def _top_evidence(result: TriMatchResult) -> list[dict[str, Any]]:
    ordered = sorted(result.evidence, key=lambda item: item.confidence, reverse=True)
    return [
        {
            "rule_id": item.rule_id,
            "dimension": item.dimension.value,
            "target": item.target,
            "layer": item.layer.value,
            "matched_text": item.matched_text,
            "confidence": item.confidence,
            "negated": item.negated,
            "hedged": item.hedged,
            "counterfactual": item.counterfactual,
        }
        for item in ordered[:5]
    ]


def _classify_comparison(active_passed: bool, staged_passed: bool) -> str:
    if active_passed and staged_passed:
        return "both_correct"
    if not active_passed and staged_passed:
        return "improvement_active_wrong_staged_correct"
    if active_passed and not staged_passed:
        return "regression_active_correct_staged_wrong"
    return "both_wrong"


def _summary(
    rows: list[dict[str, Any]],
    active_pack: RulePack,
    staged_pack: RulePack,
) -> dict[str, Any]:
    total = len(rows)
    active_correct = sum(1 for row in rows if row["active"]["passed"])
    staged_correct = sum(1 for row in rows if row["staged"]["passed"])
    classification_counts = Counter(row["classification"] for row in rows)

    by_dimension: dict[str, dict[str, Any]] = {}
    for dimension, dimension_rows in _group_by(rows, "dimension").items():
        count = len(dimension_rows)
        by_dimension[dimension] = {
            "examples": count,
            "active_correct": sum(1 for row in dimension_rows if row["active"]["passed"]),
            "staged_correct": sum(1 for row in dimension_rows if row["staged"]["passed"]),
            "active_accuracy": _ratio(
                sum(1 for row in dimension_rows if row["active"]["passed"]),
                count,
            ),
            "staged_accuracy": _ratio(
                sum(1 for row in dimension_rows if row["staged"]["passed"]),
                count,
            ),
        }

    by_subset: dict[str, dict[str, Any]] = {}
    for subset, subset_rows in _group_by(rows, "subset").items():
        count = len(subset_rows)
        by_subset[subset] = {
            "examples": count,
            "active_accuracy": _ratio(
                sum(1 for row in subset_rows if row["active"]["passed"]),
                count,
            ),
            "staged_accuracy": _ratio(
                sum(1 for row in subset_rows if row["staged"]["passed"]),
                count,
            ),
        }

    regressions = classification_counts["regression_active_correct_staged_wrong"]
    improvements = classification_counts["improvement_active_wrong_staged_correct"]

    return {
        "valid": regressions == 0 and staged_correct >= active_correct,
        "examples": total,
        "active_rule_count": len(active_pack.rules),
        "staged_rule_count": len(staged_pack.rules),
        "active_correct": active_correct,
        "staged_correct": staged_correct,
        "active_accuracy": _ratio(active_correct, total),
        "staged_accuracy": _ratio(staged_correct, total),
        "net_gain": staged_correct - active_correct,
        "classification_counts": dict(classification_counts),
        "regressions": regressions,
        "improvements": improvements,
        "disagreements": sum(1 for row in rows if row["active"]["value"] != row["staged"]["value"]),
        "by_dimension": by_dimension,
        "by_subset": by_subset,
    }


def _group_by(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[key])].append(row)
    return grouped


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]

    lines = [
        "# Tri-Match Rules Army v2 Shadow Evaluation",
        "",
        "## Summary",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Active rules: `{summary['active_rule_count']}`",
        f"- Staged Rules Army rules: `{summary['staged_rule_count']}`",
        f"- Examples: `{summary['examples']}`",
        f"- Active accuracy: `{summary['active_accuracy']}`",
        f"- Staged accuracy: `{summary['staged_accuracy']}`",
        f"- Net gain: `{summary['net_gain']}`",
        f"- Regressions: `{summary['regressions']}`",
        f"- Improvements: `{summary['improvements']}`",
        f"- Disagreements: `{summary['disagreements']}`",
        f"- Valid for next review stage: `{summary['valid']}`",
        "",
        "## By Dimension",
        "",
        "| Dimension | Examples | Active Accuracy | Staged Accuracy |",
        "|---|---:|---:|---:|",
    ]

    for dimension, item in sorted(summary["by_dimension"].items()):
        examples = item["examples"]
        active_accuracy = item["active_accuracy"]
        staged_accuracy = item["staged_accuracy"]
        lines.append(f"| `{dimension}` | {examples} | {active_accuracy} | {staged_accuracy} |")

    lines.extend(
        [
            "",
            "## Classification Counts",
            "",
            "```json",
            json.dumps(summary["classification_counts"], indent=2, sort_keys=True),
            "```",
            "",
            "## High-Risk Regressions",
            "",
        ]
    )

    if not report["high_risk_regressions"]:
        lines.append("No high-risk regressions found.")
    else:
        for row in report["high_risk_regressions"][:25]:
            lines.extend(_row_detail(row))

    lines.extend(["", "## Improvements", ""])
    if not report["improvements"]:
        lines.append("No improvements found.")
    else:
        for row in report["improvements"][:25]:
            lines.extend(_row_detail(row))

    return "\n".join(lines)


def _row_detail(row: dict[str, Any]) -> list[str]:
    return [
        f"### Example {row['index']} — {row['dimension']} / {row['subset']}",
        "",
        f"Text: {row['text']}",
        "",
        f"Expected: `{row['expected']}`",
        f"Active: `{row['active']['value']}` passed=`{row['active']['passed']}`",
        f"Staged: `{row['staged']['value']}` passed=`{row['staged']['passed']}`",
        "",
    ]


if __name__ == "__main__":
    raise SystemExit(main())
