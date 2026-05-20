"""Audit a thread-export directory or combined JSON for final source compliance.

Usage:
    python scripts/data/audit_thread_exports.py \\
        --input reports/thread_exports/latest_20260520T120000Z \\
        [--strict]

In --strict mode the script exits 1 if any deterministic final source is found.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Source prefixes that indicate a deterministic / non-Claude final response.
_BAD_DETERMINISTIC_PREFIXES = (
    "template_",
    "deterministic_",
    "clarification_",
    "portfolio_engine_",
)

# Final sources that are always forbidden (except the claude_sonnet_repair variant).
_FORBIDDEN_FINAL_SOURCES_SUFFIXES = ("_quality_fallback",)
_ALLOWED_QUALITY_FALLBACK_PREFIX = "claude_sonnet_repair"

# Trace fields that should be present in a healthy turn trace.
_REQUIRED_TRACE_FIELDS = (
    "customer_response_contract",
    "response_quality",
    "sales_tone",
    "context_pack",
    "response_plan",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit thread export traces")
    parser.add_argument("--input", required=True, help="Path to export dir or combined JSON")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if any deterministic final sources are found",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    threads = _load_threads(input_path)

    if not threads:
        print("No threads found in input.", file=sys.stderr)
        return 1

    result = _audit(threads)
    _print_report(result)
    _write_reports(result, input_path)

    if args.strict and result["deterministic_source_hits"] > 0:
        print(
            f"\nSTRICT mode: {result['deterministic_source_hits']} deterministic source(s) found.",
            file=sys.stderr,
        )
        return 1
    return 0


def _load_threads(path: Path) -> list[dict[str, Any]]:
    if path.is_file() and path.suffix == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "threads" in raw:
            return list(raw["threads"])
        if isinstance(raw, list):
            return raw
        return [raw]

    if path.is_dir():
        combined = path / "latest_threads_combined.json"
        if combined.exists():
            return _load_threads(combined)
        threads: list[dict[str, Any]] = []
        for f in sorted(path.glob("*.json")):
            if f.name == "latest_threads_combined.json":
                continue
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and "traces" in raw:
                    threads.append(raw)
            except Exception:  # noqa: BLE001,S110
                pass
        return threads

    return []


def _audit(threads: list[dict[str, Any]]) -> dict[str, Any]:
    threads_checked = len(threads)
    turns_checked = 0
    det_source_hits: list[dict[str, Any]] = []
    missing_contract_count = 0
    missing_fields_by_thread: dict[str, list[str]] = {}

    for thread in threads:
        tid = str(thread.get("thread_id", "unknown"))
        traces = thread.get("traces") or []
        for trace in traces:
            turns_checked += 1
            assistant = trace.get("assistant") or {}
            final_source = str(assistant.get("source") or "")

            # Check deterministic/bad prefix.
            if _is_bad_source(final_source):
                det_source_hits.append(
                    {
                        "thread_id": tid,
                        "final_source": final_source,
                        "recorded_at": trace.get("recorded_at", ""),
                    }
                )

            # Check customer_response_contract presence.
            contract = trace.get("customer_response_contract")
            if contract is None:
                missing_contract_count += 1

            # Check required trace fields.
            missing = [f for f in _REQUIRED_TRACE_FIELDS if f not in trace]
            if missing:
                missing_fields_by_thread.setdefault(tid, []).extend(missing)

    total_turns = turns_checked if turns_checked else 1
    compliance_rate = round(1.0 - len(det_source_hits) / total_turns, 4)

    return {
        "threads_checked": threads_checked,
        "turns_checked": turns_checked,
        "source_compliance_rate": compliance_rate,
        "deterministic_source_hits": len(det_source_hits),
        "deterministic_sources": det_source_hits[:20],
        "missing_contract_count": missing_contract_count,
        "missing_fields_summary": {
            tid: sorted(set(fields)) for tid, fields in list(missing_fields_by_thread.items())[:10]
        },
    }


def _is_bad_source(source: str) -> bool:
    if not source:
        return False
    for prefix in _BAD_DETERMINISTIC_PREFIXES:
        if source.startswith(prefix):
            return True
    for suffix in _FORBIDDEN_FINAL_SOURCES_SUFFIXES:
        if source.endswith(suffix) and not source.startswith(_ALLOWED_QUALITY_FALLBACK_PREFIX):
            return True
    return False


def _print_report(result: dict[str, Any]) -> None:
    print("\n=== Thread Export Audit ===")
    print(f"Threads checked: {result['threads_checked']}")
    print(f"Turns checked:   {result['turns_checked']}")
    print(f"Source compliance rate: {result['source_compliance_rate']:.1%}")
    print(f"Deterministic source hits: {result['deterministic_source_hits']}")
    print(f"Missing contract count: {result['missing_contract_count']}")
    if result["deterministic_sources"]:
        print("\nDeterministic sources found:")
        for hit in result["deterministic_sources"][:5]:
            print(f"  thread={hit['thread_id'][:8]} source={hit['final_source']}")
    print()


def _write_reports(result: dict[str, Any], input_path: Path) -> None:
    from datetime import UTC, datetime

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if input_path.is_dir():
        out_dir = input_path
    else:
        out_dir = input_path.parent

    json_path = out_dir / f"audit_{stamp}.json"
    md_path = out_dir / f"audit_{stamp}.md"

    json_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Thread Export Audit",
        "",
        f"- Threads checked: {result['threads_checked']}",
        f"- Turns checked: {result['turns_checked']}",
        f"- Source compliance rate: {result['source_compliance_rate']:.1%}",
        f"- Deterministic source hits: {result['deterministic_source_hits']}",
        f"- Missing contract count: {result['missing_contract_count']}",
        "",
    ]
    if result["deterministic_sources"]:
        lines.append("## Deterministic Sources Found")
        lines.append("")
        for hit in result["deterministic_sources"]:
            lines.append(f"- thread `{hit['thread_id'][:8]}`: `{hit['final_source']}`")
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"audit_json={json_path}")
    print(f"audit_md={md_path}")


if __name__ == "__main__":
    raise SystemExit(main())
