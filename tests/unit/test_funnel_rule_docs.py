from __future__ import annotations

from pathlib import Path


def test_d082_records_funnel_rule_governance_after_d081() -> None:
    text = Path("docs/adr/D-082-funnel-rule-governance-after-d081.md").read_text(encoding="utf-8")

    assert "funnel rule governance and partitioning" in text
    assert "Decision Layer weight `0`" in text
    assert "must not directly mutate `ThreadState.sales_stage`" in text
