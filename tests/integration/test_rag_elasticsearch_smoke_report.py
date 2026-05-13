from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_rag_elasticsearch_smoke_report_default_is_safe_skip() -> None:
    result = subprocess.run(  # noqa: S603 - fixed repo script under test.
        [
            sys.executable,
            "scripts/data/run_rag_elasticsearch_smoke_report.py",
            "--output-dir",
            str(ROOT / "reports" / "rag"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr

    report_path = ROOT / "reports" / "rag" / "rag_elasticsearch_smoke_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    summary = report["summary"]

    assert summary["valid"] is True
    assert summary["externals_checked"] is False
    assert summary["total_turns"] == 0
    assert len(report["cases"]) == 5


def test_rag_elasticsearch_smoke_report_contains_bypass_cases() -> None:
    result = subprocess.run(  # noqa: S603 - fixed repo script under test.
        [
            sys.executable,
            "scripts/data/run_rag_elasticsearch_smoke_report.py",
            "--output-dir",
            str(ROOT / "reports" / "rag"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr

    report_path = ROOT / "reports" / "rag" / "rag_elasticsearch_smoke_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))

    cases = {case["name"]: case for case in report["cases"]}

    assert cases["pricing_rag_bypass_smoke"]["expect_chunks"] is False
    assert cases["timeline_rag_bypass_smoke"]["expect_chunks"] is False
    assert cases["ghostwriting_service_smoke"]["expect_chunks"] is True
