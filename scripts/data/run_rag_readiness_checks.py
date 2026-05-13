from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ReadinessCommand:
    name: str
    command: list[str]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run CI-safe RAG readiness checks without external services."
    )
    parser.add_argument("--output-dir", default="reports/rag")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report = build_report()

    json_path = output_dir / "rag_readiness_checks_report.json"
    md_path = output_dir / "rag_readiness_checks_report.md"

    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(markdown(report), encoding="utf-8")

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")

    return 0 if report["summary"]["valid"] else 1


def build_report() -> dict[str, Any]:
    commands = readiness_commands()
    results = [run_command(item) for item in commands]

    failed = [item for item in results if item["returncode"] != 0]

    summary = {
        "valid": not failed,
        "generated_at": datetime.now(UTC).isoformat(),
        "command_count": len(results),
        "passed_count": len(results) - len(failed),
        "failed_count": len(failed),
        "externals_required": False,
        "creates_elasticsearch_index": False,
        "swaps_alias": False,
        "embeds_source_documents": False,
        "safety_note": (
            "CI-safe RAG readiness only. This does not require Elasticsearch or TEI, "
            "does not create indices, does not embed source documents, does not bulk "
            "index documents, and does not move aliases."
        ),
    }

    return {
        "schema_version": 1,
        "summary": summary,
        "commands": results,
    }


def readiness_commands() -> list[ReadinessCommand]:
    return [
        ReadinessCommand(
            name="rag_source_metadata_strict",
            command=[
                sys.executable,
                "scripts/data/verify_rag_source_metadata.py",
                "--strict",
            ],
        ),
        ReadinessCommand(
            name="rag_index_build_report",
            command=[
                sys.executable,
                "scripts/data/build_rag_index_build_report.py",
            ],
        ),
        ReadinessCommand(
            name="rag_elasticsearch_indexer_dry_run",
            command=[
                sys.executable,
                "scripts/data/build_rag_elasticsearch_index.py",
            ],
        ),
        ReadinessCommand(
            name="rag_elasticsearch_smoke_safe_skip",
            command=[
                sys.executable,
                "scripts/data/run_rag_elasticsearch_smoke_report.py",
            ],
        ),
    ]


def run_command(item: ReadinessCommand) -> dict[str, Any]:
    result = subprocess.run(  # noqa: S603 - fixed repo commands.
        item.command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    return {
        "name": item.name,
        "command": item.command,
        "returncode": result.returncode,
        "passed": result.returncode == 0,
        "stdout_tail": tail(result.stdout),
        "stderr_tail": tail(result.stderr),
    }


def tail(value: str, *, max_chars: int = 4000) -> str:
    value = value.strip()
    if len(value) <= max_chars:
        return value
    return value[-max_chars:]


def markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# RAG Readiness Checks Report",
        "",
        "## Summary",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Valid: `{summary['valid']}`",
        f"- Commands: `{summary['command_count']}`",
        f"- Passed: `{summary['passed_count']}`",
        f"- Failed: `{summary['failed_count']}`",
        f"- Externals required: `{summary['externals_required']}`",
        f"- Creates Elasticsearch index: `{summary['creates_elasticsearch_index']}`",
        f"- Swaps alias: `{summary['swaps_alias']}`",
        f"- Embeds source documents: `{summary['embeds_source_documents']}`",
        "",
        "## Safety Note",
        "",
        summary["safety_note"],
        "",
        "## Commands",
        "",
        "| Name | Passed | Return Code |",
        "|---|---:|---:|",
    ]

    for item in report["commands"]:
        lines.append(f"| `{item['name']}` | `{item['passed']}` | `{item['returncode']}` |")

    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
