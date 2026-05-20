from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
_RUNNER_PATH = ROOT / "tests" / "evals" / "conversation_runner.py"
_RUNNER_SPEC = importlib.util.spec_from_file_location("conversation_runner", _RUNNER_PATH)
assert _RUNNER_SPEC is not None and _RUNNER_SPEC.loader is not None
_RUNNER_MODULE = importlib.util.module_from_spec(_RUNNER_SPEC)
_RUNNER_SPEC.loader.exec_module(_RUNNER_MODULE)
run_all_cases = _RUNNER_MODULE.run_all_cases

CASE_DIR = Path("tests/evals/conversations")
REPORT_DIR = Path("reports/conversation_evals")

# Phase 12 capability → case IDs that exercise it.
PHASE12_CAPABILITY_CASES: dict[str, list[str]] = {
    "claude_only_contract": [
        "cover_design_children_fiction",
        "nda_agreement_negation",
        "counterfactual_safety",
        "target_bound_negation",
        "delegated_cover_style",
    ],
    "project_shift": [
        "new_project_shift",
        "same_project_service_bundle",
        "multi_project_memory",
    ],
    "target_bound_negation": ["target_bound_negation"],
    "delegated_slots": ["delegated_cover_style"],
    "portfolio_fallback": ["portfolio_fallback_samples"],
    "flexible_intent": ["flexible_service_guidance", "bookcraft_discretion_consultation"],
    "project_aware_rag": ["new_project_shift", "multi_project_memory"],
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run conversation eval report")
    parser.add_argument(
        "--no-fail-on-errors",
        action="store_true",
        help="Exit 0 even when cases fail (useful for CI report-only runs)",
    )
    args = parser.parse_args()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now(UTC)
    results = run_all_cases(CASE_DIR)
    finished_at = datetime.now(UTC)

    summary = _summary(results, started_at=started_at, finished_at=finished_at)
    phase12 = _phase12_readiness(results)
    payload = {
        "summary": summary,
        "phase12_readiness": phase12,
        "results": [result.model_dump(mode="json") for result in results],
    }

    stamp = started_at.strftime("%Y%m%dT%H%M%SZ")
    json_path = REPORT_DIR / f"{stamp}.json"
    md_path = REPORT_DIR / f"{stamp}.md"

    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(_markdown(payload), encoding="utf-8")

    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")

    if args.no_fail_on_errors:
        return 0
    return 0 if summary["passed"] else 1


def _summary(
    results: list[Any],
    *,
    started_at: datetime,
    finished_at: datetime,
) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for item in results if item.passed)
    failed = total - passed

    def _metric_avg(name: str) -> float:
        if total == 0:
            return 0.0
        values = [float(item.metrics.get(name, 0.0)) for item in results]
        return round(sum(values) / total, 4)

    def _metric_sum(name: str) -> int:
        return sum(int(item.metrics.get(name, 0)) for item in results)

    return {
        "case_count": total,
        "passed_count": passed,
        "failed_count": failed,
        "passed": failed == 0,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "elapsed_seconds": round((finished_at - started_at).total_seconds(), 3),
        # Core metrics
        "intent_accuracy_avg": _metric_avg("intent_accuracy"),
        "service_accuracy_avg": _metric_avg("service_accuracy"),
        "context_retention_score_avg": _metric_avg("context_retention_score"),
        "avg_latency_ms_avg": _metric_avg("avg_latency_ms"),
        # Phase 12 metrics
        "claude_only_response_rate_avg": _metric_avg("claude_only_response_rate"),
        "project_shift_accuracy_avg": _metric_avg("project_shift_accuracy"),
        "negation_target_accuracy_avg": _metric_avg("negation_target_accuracy"),
        "delegated_slot_reask_violations_total": _metric_sum("delegated_slot_reask_violations"),
        "portfolio_fallback_accuracy_avg": _metric_avg("portfolio_fallback_accuracy"),
        "flexible_intent_accuracy_avg": _metric_avg("flexible_intent_accuracy"),
        "project_aware_rag_accuracy_avg": _metric_avg("project_aware_rag_accuracy"),
        "tool_safety_violations_total": _metric_sum("tool_safety_violations"),
        "internal_artifact_violations_total": _metric_sum("internal_artifact_violations"),
    }


def _phase12_readiness(results: list[Any]) -> dict[str, Any]:
    by_id = {r.case_id: r for r in results}
    readiness: dict[str, Any] = {}

    for capability, cases in PHASE12_CAPABILITY_CASES.items():
        present = [c for c in cases if c in by_id]
        passed_cases = [c for c in present if by_id[c].passed]
        readiness[capability] = {
            "cases_checked": len(present),
            "cases_passed": len(passed_cases),
            "ready": len(present) > 0 and len(passed_cases) == len(present),
            "failed_cases": [c for c in present if not by_id[c].passed],
        }

    overall_ready = all(v["ready"] for v in readiness.values() if v["cases_checked"] > 0)
    readiness["overall_ready"] = overall_ready
    return readiness


def _markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    phase12 = payload.get("phase12_readiness", {})

    lines = [
        "# Conversation Eval Report",
        "",
        f"- Cases: {summary['case_count']}",
        f"- Passed: {summary['passed_count']}",
        f"- Failed: {summary['failed_count']}",
        f"- Overall: {'PASS' if summary['passed'] else 'FAIL'}",
        "",
        "## Core Metrics",
        "",
        f"- Intent Accuracy (avg): {summary['intent_accuracy_avg']}",
        f"- Service Accuracy (avg): {summary['service_accuracy_avg']}",
        f"- Context Retention (avg): {summary['context_retention_score_avg']}",
        f"- Avg Latency ms (avg): {summary['avg_latency_ms_avg']}",
        "",
        "## Phase 12 Metrics",
        "",
        f"- Claude-only Response Rate (avg): {summary['claude_only_response_rate_avg']}",
        f"- Project Shift Accuracy (avg): {summary['project_shift_accuracy_avg']}",
        f"- Negation Target Accuracy (avg): {summary['negation_target_accuracy_avg']}",
        f"- Delegated Slot Re-ask Violations: {summary['delegated_slot_reask_violations_total']}",
        f"- Portfolio Fallback Accuracy (avg): {summary['portfolio_fallback_accuracy_avg']}",
        f"- Flexible Intent Accuracy (avg): {summary['flexible_intent_accuracy_avg']}",
        f"- Project-aware RAG Accuracy (avg): {summary['project_aware_rag_accuracy_avg']}",
        f"- Tool Safety Violations: {summary['tool_safety_violations_total']}",
        f"- Internal Artifact Violations: {summary['internal_artifact_violations_total']}",
        "",
        "## Phase 12 Readiness",
        "",
    ]

    for capability, status in phase12.items():
        if capability == "overall_ready":
            continue
        if not isinstance(status, dict):
            continue
        icon = "✓" if status.get("ready") else "✗"
        lines.append(
            f"- {icon} **{capability}**: "
            f"{status['cases_passed']}/{status['cases_checked']} cases passed"
        )
        for failed in status.get("failed_cases", []):
            lines.append(f"  - FAIL: {failed}")

    overall = phase12.get("overall_ready", False)
    lines += [
        "",
        f"**Phase 12 Overall: {'READY' if overall else 'NOT READY'}**",
        "",
        "## Cases",
        "",
    ]

    for result in payload["results"]:
        lines.append(
            f"- `{result['case_id']}`: {'PASS' if result['passed'] else 'FAIL'} "
            f"(turns={result['total_turns']}, failures={len(result['failures'])})"
        )
        if result["failures"]:
            lines.append(f"  - First failure: {result['failures'][0]}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
