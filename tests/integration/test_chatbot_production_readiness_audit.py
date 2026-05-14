from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_chatbot_production_readiness_audit_safe_default() -> None:
    env = {
        **os.environ,
        "APP_ENV": "test",
        "LLM_PROVIDER_MODE": "mock",
        "API_AUTH_MODE": "off",
        "NDA_MODE": "manual",
        "AGREEMENT_MODE": "manual",
        "METRICS_PUBLIC": "false",
    }

    result = subprocess.run(  # noqa: S603 - fixed repo script under test.
        [
            sys.executable,
            "scripts/data/run_chatbot_production_readiness_audit.py",
            "--output-dir",
            str(ROOT / "reports" / "chatbot"),
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr

    report_path = ROOT / "reports" / "chatbot" / "production_readiness_audit_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    summary = report["summary"]

    assert summary["valid"] is True
    assert summary["error_count"] == 0
    assert summary["blind_full_production_ready"] is False

    check_names = {check["name"] for check in report["checks"]}
    assert "file_exists:scripts/data/run_chatbot_complex_message_diagnostics.py" in check_names
    assert "nda_not_autonomous" in check_names
    assert "agreement_not_autonomous" in check_names
    assert "rag_alias_configured" in check_names
