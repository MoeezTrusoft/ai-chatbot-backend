from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKLIST = ROOT / "docs" / "runbooks" / "final-launch-readiness-checklist.md"


def test_final_launch_readiness_checklist_exists_and_links_core_runbooks() -> None:
    text = CHECKLIST.read_text(encoding="utf-8")

    required_phrases = [
        "chatbot-complex-message-diagnostics.md",
        "chatbot-production-readiness-audit.md",
        "live-mode-readiness-audit.md",
        "api-smoke-runbook.md",
        "observability-collector-readiness.md",
        "rag-production-rollout-runbook.md",
        "rag-elasticsearch-smoke-report.md",
        "Production candidate: YES",
        "Controlled staging ready: YES",
        "Blind full production ready: NOT YET",
        "Go / No-Go Criteria",
        "Rollback Actions",
        "Post-Launch Monitoring",
    ]

    for phrase in required_phrases:
        assert phrase in text
