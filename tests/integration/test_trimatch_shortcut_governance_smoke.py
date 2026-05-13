from __future__ import annotations

from pathlib import Path

from bookcraft.infra.config import Settings


def test_shortcut_candidate_mode_is_consideration_only() -> None:
    settings = Settings(app_env="test", trimatch_extra_mode="shortcut_candidate")

    assert settings.trimatch_extra_mode == "shortcut_candidate"


def test_shortcut_design_blocks_sensitive_query_intents() -> None:
    design = Path("docs/architecture/trimatch-shortcut-mode-design.md").read_text(encoding="utf-8")

    required_blocked_intents = [
        "pricing_question",
        "timeline_question",
        "portfolio_request",
        "nda_request",
        "agreement_request",
        "payment_question",
        "complaint_or_objection",
        "ready_to_buy",
        "spam_or_abuse",
        "off_topic",
    ]

    for blocked_intent in required_blocked_intents:
        assert blocked_intent in design


def test_shortcut_design_requires_exact_or_regex_only() -> None:
    design = Path("docs/architecture/trimatch-shortcut-mode-design.md").read_text(encoding="utf-8")

    assert "exact rules" in design
    assert "regex rules" in design
    assert "semantic-only matching" in design
    assert "fuzzy matching" in design
    assert "rules with shortcut_allowed=false" in design


def test_shortcut_design_requires_side_effects_disabled() -> None:
    design = Path("docs/architecture/trimatch-shortcut-mode-design.md").read_text(encoding="utf-8")

    assert "trimatch.extra_shortcut_considered" in design
    assert '"side_effects_allowed": false' in design
    assert '"shortcut_applied": true' in design
    assert "Do not implement shortcut mode yet." in design


def test_shortcut_design_forbids_direct_tool_and_generation_bypass() -> None:
    design = Path("docs/architecture/trimatch-shortcut-mode-design.md").read_text(encoding="utf-8")

    forbidden_calls = [
        "pricing_engine.quote",
        "portfolio_engine.request_samples",
        "document templates",
        "rag_retriever.retrieve",
        "response_generator.generate",
    ]

    for forbidden_call in forbidden_calls:
        assert forbidden_call in design


def test_shortcut_readiness_runbook_references_required_reports() -> None:
    runbook = Path("docs/runbooks/trimatch-shortcut-mode-readiness.md").read_text(encoding="utf-8")

    required_commands = [
        "run_trimatch_tiebreaker_audit_report.py",
        "run_trimatch_advisory_audit_report.py",
        "run_trimatch_shadow_runtime_review.py",
        "build_trimatch_review_ingestion_audit_report.py",
        "validate_trimatch_reinforcement.py",
        "build_trimatch_calibration_report.py",
        "test_trimatch_tiebreaker_application_governance_smoke.py",
        "test_trimatch_tiebreaker_application_gated.py",
        "test_trimatch_tiebreaker_eligibility_evaluator.py",
        "test_trimatch_reinforcement_governance_smoke.py",
    ]

    for command in required_commands:
        assert command in runbook


def test_shortcut_readiness_runbook_defines_safe_branch_order() -> None:
    runbook = Path("docs/runbooks/trimatch-shortcut-mode-readiness.md").read_text(encoding="utf-8")

    required_branch_order = [
        "docs/trimatch-shortcut-mode-design",
        "test/trimatch-shortcut-governance-smoke",
        "feat/trimatch-shortcut-candidate-considered",
        "feat/trimatch-shortcut-audit-report",
        "feat/trimatch-shortcut-application-gated",
    ]

    for branch_name in required_branch_order:
        assert branch_name in runbook
