from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs" / "runbooks" / "api-smoke-runbook.md"


def test_api_smoke_runbook_seeded_customer_section_is_well_formed() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "heredoc>" not in text
    assert "bquote>" not in text
    assert '--customer-id "$SMOKE_CUSTOMER_ID"' in text
    assert "The customer must already exist in the `customers` table." in text
    assert text.count("```") % 2 == 0
