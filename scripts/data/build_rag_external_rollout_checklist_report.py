from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bookcraft.infra.config import Settings

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class RolloutCommand:
    name: str
    command: str
    purpose: str
    creates_index: bool = False
    moves_alias: bool = False
    requires_externals: bool = False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build an operator-facing RAG external rollout checklist report."
    )
    parser.add_argument("--output-dir", default="reports/rag")
    parser.add_argument(
        "--check-externals",
        action="store_true",
        help="Check Elasticsearch/TEI reachability through existing readiness report.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings()
    report = build_report(
        settings=settings,
        check_externals=args.check_externals,
    )

    json_path = output_dir / "rag_external_rollout_checklist_report.json"
    md_path = output_dir / "rag_external_rollout_checklist_report.md"

    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(markdown(report), encoding="utf-8")

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")

    return 0 if report["summary"]["valid"] else 1


def build_report(
    *,
    settings: Settings,
    check_externals: bool,
) -> dict[str, Any]:
    env_summary = {
        "elasticsearch_url": settings.elasticsearch_url,
        "tei_url": settings.tei_url,
        "rag_index_alias": settings.rag_index_alias,
        "rag_index_version": settings.rag_index_version,
        "rag_source_dir": settings.rag_source_dir,
        "rag_build_dir": settings.rag_build_dir,
        "embedding_dimensions": settings.embedding_dimensions,
        "tei_batch_size": settings.tei_batch_size,
    }

    preflight_results = [
        run_command(
            name="rag_readiness_checks",
            command=[
                sys.executable,
                "scripts/data/run_rag_readiness_checks.py",
            ],
        ),
        run_command(
            name="rag_source_metadata_strict",
            command=[
                sys.executable,
                "scripts/data/verify_rag_source_metadata.py",
                "--strict",
            ],
        ),
        run_command(
            name="rag_index_build_report",
            command=[
                sys.executable,
                "scripts/data/build_rag_index_build_report.py",
            ],
        ),
    ]

    external_result: dict[str, Any] | None = None
    if check_externals:
        external_result = run_command(
            name="rag_index_build_report_check_externals",
            command=[
                sys.executable,
                "scripts/data/build_rag_index_build_report.py",
                "--check-externals",
            ],
        )

    failed_preflight = [item for item in preflight_results if item["returncode"] != 0]
    external_failed = external_result is not None and external_result["returncode"] != 0

    commands = rollout_commands(settings)

    summary = {
        "valid": not failed_preflight and not external_failed,
        "generated_at": datetime.now(UTC).isoformat(),
        "check_externals": check_externals,
        "preflight_count": len(preflight_results),
        "preflight_failed_count": len(failed_preflight),
        "external_checked": external_result is not None,
        "external_failed": external_failed,
        "rollout_command_count": len(commands),
        "creates_index": False,
        "moves_alias": False,
        "embeds_source_documents": False,
        "safety_note": (
            "Checklist/report only. This script does not create Elasticsearch "
            "indices, embed source documents, bulk index content, move aliases, "
            "or enable production RAG."
        ),
    }

    return {
        "schema_version": 1,
        "summary": summary,
        "environment": env_summary,
        "preflight_results": preflight_results,
        "external_result": external_result,
        "rollout_commands": [command_to_dict(item) for item in commands],
        "stop_conditions": stop_conditions(),
        "rollback": rollback_instructions(settings),
    }


def run_command(
    *,
    name: str,
    command: list[str],
) -> dict[str, Any]:
    result = subprocess.run(  # noqa: S603 - fixed repo commands.
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ},
    )

    return {
        "name": name,
        "command": command,
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


def rollout_commands(settings: Settings) -> list[RolloutCommand]:
    return [
        RolloutCommand(
            name="preflight_readiness",
            command="uv run python scripts/data/run_rag_readiness_checks.py",
            purpose="Confirm CI-safe RAG readiness before touching externals.",
        ),
        RolloutCommand(
            name="external_connectivity",
            command=(
                "uv run python scripts/data/build_rag_index_build_report.py --check-externals"
            ),
            purpose="Confirm Elasticsearch and TEI are reachable.",
            requires_externals=True,
        ),
        RolloutCommand(
            name="candidate_index_build",
            command="uv run python scripts/data/build_rag_elasticsearch_index.py --apply",
            purpose="Create a versioned candidate index without moving the live alias.",
            creates_index=True,
            requires_externals=True,
        ),
        RolloutCommand(
            name="candidate_smoke",
            command=(
                "RAG_INDEX_ALIAS=<candidate_index_name> "
                "uv run python scripts/data/run_rag_elasticsearch_smoke_report.py "
                "--check-externals --require-externals"
            ),
            purpose="Smoke test the candidate index before alias swap.",
            requires_externals=True,
        ),
        RolloutCommand(
            name="alias_swap",
            command=(
                "uv run python scripts/data/build_rag_elasticsearch_index.py --apply --swap-alias"
            ),
            purpose=f"Move {settings.rag_index_alias} only after candidate smoke passes.",
            creates_index=True,
            moves_alias=True,
            requires_externals=True,
        ),
        RolloutCommand(
            name="live_alias_smoke",
            command=(
                "uv run python scripts/data/run_rag_elasticsearch_smoke_report.py "
                "--check-externals --require-externals"
            ),
            purpose="Validate retrieval through the live alias after alias swap.",
            requires_externals=True,
        ),
    ]


def command_to_dict(item: RolloutCommand) -> dict[str, Any]:
    return {
        "name": item.name,
        "command": item.command,
        "purpose": item.purpose,
        "creates_index": item.creates_index,
        "moves_alias": item.moves_alias,
        "requires_externals": item.requires_externals,
    }


def stop_conditions() -> list[str]:
    return [
        "metadata verifier fails",
        "RAG readiness checks fail",
        "Elasticsearch is unreachable",
        "TEI is unreachable",
        "embedding dimension mismatch",
        "candidate index build fails",
        "bulk indexing errors appear",
        "candidate smoke report is invalid",
        "pricing query returns RAG chunks",
        "timeline query returns RAG chunks",
        "alias swap fails",
        "live alias smoke report is invalid",
    ]


def rollback_instructions(settings: Settings) -> dict[str, Any]:
    return {
        "strategy": "alias_only",
        "alias": settings.rag_index_alias,
        "instruction": (
            f"Move {settings.rag_index_alias} back to the previous healthy index. "
            "Do not delete the failed candidate index until reports and logs are reviewed."
        ),
    }


def markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    environment = report["environment"]

    lines = [
        "# RAG External Rollout Checklist Report",
        "",
        "## Summary",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Valid: `{summary['valid']}`",
        f"- Check externals: `{summary['check_externals']}`",
        f"- Preflight checks: `{summary['preflight_count']}`",
        f"- Preflight failures: `{summary['preflight_failed_count']}`",
        f"- External checked: `{summary['external_checked']}`",
        f"- External failed: `{summary['external_failed']}`",
        f"- Creates index: `{summary['creates_index']}`",
        f"- Moves alias: `{summary['moves_alias']}`",
        f"- Embeds source documents: `{summary['embeds_source_documents']}`",
        "",
        "## Safety Note",
        "",
        summary["safety_note"],
        "",
        "## Environment",
        "",
        f"- Elasticsearch URL: `{environment['elasticsearch_url']}`",
        f"- TEI URL: `{environment['tei_url']}`",
        f"- RAG alias: `{environment['rag_index_alias']}`",
        f"- RAG index version: `{environment['rag_index_version']}`",
        f"- RAG source dir: `{environment['rag_source_dir']}`",
        f"- Embedding dimensions: `{environment['embedding_dimensions']}`",
        "",
        "## Preflight Results",
        "",
        "| Name | Passed | Return Code |",
        "|---|---:|---:|",
    ]

    for item in report["preflight_results"]:
        lines.append(f"| `{item['name']}` | `{item['passed']}` | `{item['returncode']}` |")

    lines.extend(
        [
            "",
            "## Rollout Commands",
            "",
            "| Step | Requires Externals | Creates Index | Moves Alias | Command |",
            "|---|---:|---:|---:|---|",
        ]
    )

    for item in report["rollout_commands"]:
        lines.append(
            "| `{name}` | `{requires}` | `{creates}` | `{moves}` | `{command}` |".format(
                name=item["name"],
                requires=item["requires_externals"],
                creates=item["creates_index"],
                moves=item["moves_alias"],
                command=item["command"],
            )
        )

    lines.extend(
        [
            "",
            "## Stop Conditions",
            "",
        ]
    )

    for condition in report["stop_conditions"]:
        lines.append(f"- {condition}")

    lines.extend(
        [
            "",
            "## Rollback",
            "",
            f"- Strategy: `{report['rollback']['strategy']}`",
            f"- Alias: `{report['rollback']['alias']}`",
            f"- Instruction: {report['rollback']['instruction']}",
            "",
        ]
    )

    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
