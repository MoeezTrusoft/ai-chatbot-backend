from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bookcraft.components.trimatch.schemas import RulePack


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a Tri-Match calibration report from shadow, review, and flow evidence."
    )
    parser.add_argument(
        "--shadow-report",
        default="reports/trimatch/rules_army_v2_shadow_eval.json",
    )
    parser.add_argument(
        "--reinforcement-root",
        default="data/trimatch/reinforcement",
    )
    parser.add_argument(
        "--production-flow-dir",
        default="reports/production-flow",
    )
    parser.add_argument(
        "--output-dir",
        default="reports/trimatch",
    )
    args = parser.parse_args()

    report = _build_report(args)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "trimatch_calibration_report.json"
    md_path = output_dir / "trimatch_calibration_report.md"

    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")
    return 0


def _build_report(args: argparse.Namespace) -> dict[str, Any]:
    shadow = _load_json_if_exists(Path(args.shadow_report))
    reinforcement_root = Path(args.reinforcement_root)

    candidates = _load_jsonl_many(reinforcement_root / "candidates")
    reviews = _load_jsonl_many(reinforcement_root / "reviews")
    compiled_rulepacks = _load_rulepacks(reinforcement_root / "staged_from_reviews")
    production_flow = _latest_production_flow(Path(args.production_flow_dir))

    candidate_status_counts = Counter(str(item.get("status")) for item in candidates)
    candidate_type_counts = Counter(str(item.get("candidate_type")) for item in candidates)
    review_decision_counts = Counter(str(item.get("decision")) for item in reviews)

    shadow_summary = shadow.get("summary", {}) if isinstance(shadow, dict) else {}
    production_summary = (
        production_flow.get("summary", {}) if isinstance(production_flow, dict) else {}
    )

    compiled_rule_count = sum(len(pack.rules) for pack in compiled_rulepacks)
    approved_reviews = (
        review_decision_counts["approve"] + review_decision_counts["edit_and_approve"]
    )

    recommendation = _recommendation(
        shadow_summary=shadow_summary,
        production_summary=production_summary,
        compiled_rule_count=compiled_rule_count,
        approved_reviews=approved_reviews,
    )

    summary = {
        "valid": True,
        "generated_at": datetime.now(UTC).isoformat(),
        "shadow_report_found": bool(shadow),
        "production_flow_report_found": bool(production_flow),
        "candidate_count": len(candidates),
        "review_count": len(reviews),
        "approved_review_count": approved_reviews,
        "compiled_rulepack_count": len(compiled_rulepacks),
        "compiled_rule_count": compiled_rule_count,
        "shadow_active_accuracy": shadow_summary.get("active_accuracy"),
        "shadow_staged_accuracy": shadow_summary.get("staged_accuracy"),
        "shadow_regressions": shadow_summary.get("regressions"),
        "shadow_improvements": shadow_summary.get("improvements"),
        "production_flow_failed_turns": production_summary.get("failed_turns"),
        "production_flow_safety_failures": production_summary.get("safety_failures"),
        "recommendation": recommendation,
    }

    return {
        "schema_version": 1,
        "summary": summary,
        "shadow_summary": shadow_summary,
        "production_flow_summary": production_summary,
        "reinforcement": {
            "candidate_status_counts": dict(candidate_status_counts),
            "candidate_type_counts": dict(candidate_type_counts),
            "review_decision_counts": dict(review_decision_counts),
            "compiled_rulepacks": [
                {
                    "version": pack.version,
                    "rule_count": len(pack.rules),
                }
                for pack in compiled_rulepacks
            ],
        },
    }


def _recommendation(
    *,
    shadow_summary: dict[str, Any],
    production_summary: dict[str, Any],
    compiled_rule_count: int,
    approved_reviews: int,
) -> str:
    shadow_regressions = int(shadow_summary.get("regressions") or 0)
    shadow_staged_accuracy = float(shadow_summary.get("staged_accuracy") or 0.0)
    safety_failures = int(production_summary.get("safety_failures") or 0)

    if shadow_regressions > 0:
        return "hold: shadow regressions must be resolved before promotion"
    if safety_failures > 0:
        return "hold: production-flow safety failures must be resolved before promotion"
    if compiled_rule_count == 0 or approved_reviews == 0:
        return "hold: no approved compiled reinforcement rules are ready"
    if shadow_staged_accuracy >= 0.95:
        return "ready_for_shadow_runtime_review"
    return "continue_collecting_evidence"


def _load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


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


def _load_rulepacks(directory: Path) -> list[RulePack]:
    packs: list[RulePack] = []
    if not directory.exists():
        return packs
    for path in sorted(directory.glob("*.json")):
        loaded = json.loads(path.read_text(encoding="utf-8"))
        packs.append(RulePack.model_validate(loaded))
    return packs


def _latest_production_flow(directory: Path) -> dict[str, Any]:
    if not directory.exists():
        return {}
    reports = sorted(directory.glob("production_flow_50_*.json"))
    if not reports:
        return {}
    return _load_json_if_exists(reports[-1])


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    reinforcement = report["reinforcement"]

    lines = [
        "# Tri-Match Calibration Report",
        "",
        "## Summary",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Shadow report found: `{summary['shadow_report_found']}`",
        f"- Production-flow report found: `{summary['production_flow_report_found']}`",
        f"- Candidates: `{summary['candidate_count']}`",
        f"- Reviews: `{summary['review_count']}`",
        f"- Approved reviews: `{summary['approved_review_count']}`",
        f"- Compiled RulePacks: `{summary['compiled_rulepack_count']}`",
        f"- Compiled rules: `{summary['compiled_rule_count']}`",
        f"- Shadow active accuracy: `{summary['shadow_active_accuracy']}`",
        f"- Shadow staged accuracy: `{summary['shadow_staged_accuracy']}`",
        f"- Shadow regressions: `{summary['shadow_regressions']}`",
        f"- Shadow improvements: `{summary['shadow_improvements']}`",
        f"- Production-flow failed turns: `{summary['production_flow_failed_turns']}`",
        f"- Production-flow safety failures: `{summary['production_flow_safety_failures']}`",
        f"- Recommendation: `{summary['recommendation']}`",
        "",
        "## Candidate Status Counts",
        "",
        "```json",
        json.dumps(
            reinforcement["candidate_status_counts"],
            indent=2,
            sort_keys=True,
        ),
        "```",
        "",
        "## Review Decision Counts",
        "",
        "```json",
        json.dumps(
            reinforcement["review_decision_counts"],
            indent=2,
            sort_keys=True,
        ),
        "```",
        "",
        "## Safety Note",
        "",
        "This report is observational. It does not activate Rules Army v2 or any "
        "approved candidate RulePack.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
