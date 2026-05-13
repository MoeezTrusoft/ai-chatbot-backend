from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_complex_message_diagnostic_report_runs_without_external_rag() -> None:
    result = subprocess.run(  # noqa: S603 - fixed repo script under test.
        [
            sys.executable,
            "scripts/data/run_chatbot_complex_message_diagnostics.py",
            "--output-dir",
            str(ROOT / "reports" / "chatbot"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr

    report_path = ROOT / "reports" / "chatbot" / "complex_message_diagnostic_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    summary = report["summary"]

    assert summary["message_count"] == 50
    assert summary["trg_failed_count"] == 0
    assert summary["trg_missing_count"] == 0
    assert summary["trimatch_missing_count"] == 0
    assert summary["intent_missing_count"] == 0
    assert len(report["turns"]) == 50
