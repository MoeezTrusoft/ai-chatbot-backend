from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_rag_readiness_checks_are_ci_safe() -> None:
    result = subprocess.run(  # noqa: S603 - fixed repo script under test.
        [
            sys.executable,
            "scripts/data/run_rag_readiness_checks.py",
            "--output-dir",
            str(ROOT / "reports" / "rag"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr

    report_path = ROOT / "reports" / "rag" / "rag_readiness_checks_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    summary = report["summary"]

    assert summary["valid"] is True
    assert summary["command_count"] == 4
    assert summary["failed_count"] == 0
    assert summary["externals_required"] is False
    assert summary["creates_elasticsearch_index"] is False
    assert summary["swaps_alias"] is False
    assert summary["embeds_source_documents"] is False

    command_names = {item["name"] for item in report["commands"]}
    assert command_names == {
        "rag_source_metadata_strict",
        "rag_index_build_report",
        "rag_elasticsearch_indexer_dry_run",
        "rag_elasticsearch_smoke_safe_skip",
    }
