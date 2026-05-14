from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_api_smoke_report_safe_without_base_url() -> None:
    result = subprocess.run(  # noqa: S603 - fixed repo script under test.
        [
            sys.executable,
            "scripts/data/run_api_smoke_report.py",
            "--output-dir",
            str(ROOT / "reports" / "chatbot"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr

    report_path = ROOT / "reports" / "chatbot" / "api_smoke_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    summary = report["summary"]

    assert summary["valid"] is True
    assert summary["base_url"] is None
    assert summary["skipped_count"] == 1
    assert report["checks"][0]["name"] == "base_url_required"
