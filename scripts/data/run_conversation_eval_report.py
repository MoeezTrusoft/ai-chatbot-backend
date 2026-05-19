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
    payload = {
        "summary": summary,
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

    return {
        "case_count": total,
        "passed_count": passed,
        "failed_count": failed,
        "passed": failed == 0,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "elapsed_seconds": round((finished_at - started_at).total_seconds(), 3),
        "intent_accuracy_avg": _metric_avg("intent_accuracy"),
        "service_accuracy_avg": _metric_avg("service_accuracy"),
        "context_retention_score_avg": _metric_avg("context_retention_score"),
        "avg_latency_ms_avg": _metric_avg("avg_latency_ms"),
    }


def _markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Conversation Eval Report",
        "",
        f"- Cases: {summary['case_count']}",
        f"- Passed: {summary['passed_count']}",
        f"- Failed: {summary['failed_count']}",
        f"- Overall: {'PASS' if summary['passed'] else 'FAIL'}",
        f"- Intent Accuracy (avg): {summary['intent_accuracy_avg']}",
        f"- Service Accuracy (avg): {summary['service_accuracy_avg']}",
        f"- Context Retention (avg): {summary['context_retention_score_avg']}",
        f"- Avg Latency ms (avg): {summary['avg_latency_ms_avg']}",
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
