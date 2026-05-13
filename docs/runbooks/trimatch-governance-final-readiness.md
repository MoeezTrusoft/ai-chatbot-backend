
Tri-Match Governance Final Readiness Runbook
Purpose

This runbook is the final operator checklist for the Tri-Match governance ladder.

It confirms the system is ready for controlled environment-level rollout, not automatic production enablement.

One-command readiness sequence
uv run python scripts/data/run_trimatch_shortcut_audit_report.py
uv run python scripts/data/run_trimatch_tiebreaker_audit_report.py
uv run python scripts/data/run_trimatch_advisory_audit_report.py
uv run python scripts/data/run_trimatch_shadow_runtime_review.py
uv run python scripts/data/build_trimatch_review_ingestion_audit_report.py
uv run python scripts/data/validate_trimatch_reinforcement.py
uv run python scripts/data/build_trimatch_calibration_report.py
uv run pytest tests/integration/test_trimatch_shortcut_application_governance_smoke.py -q
uv run pytest tests/integration/test_trimatch_tiebreaker_application_governance_smoke.py -q
uv run pytest tests/integration/test_trimatch_reinforcement_governance_smoke.py -q
uv run mypy src
Mode rollout
1. Off
TRIMATCH_EXTRA_MODE=off

Safe baseline.

2. Shadow
TRIMATCH_EXTRA_MODE=shadow

Observation only.

3. Advisory
TRIMATCH_EXTRA_MODE=advisory

Recommendation only.

4. Tiebreaker candidate
TRIMATCH_EXTRA_MODE=tiebreaker_candidate

Governed intent tiebreaker.

5. Shortcut candidate
TRIMATCH_EXTRA_MODE=shortcut_candidate

Gated exact/regex shortcut application only.

Stop conditions

Stop rollout immediately if any are true:

side_effects_allowed_count > 0
pricing-sensitive shortcut applied
document-sensitive shortcut applied
portfolio-sensitive shortcut applied
semantic/fuzzy shortcut applied
negated/counterfactual shortcut applied
unreviewed rule applied
missing rule_id applied
response generation bypass detected
pricing engine bypass detected
portfolio registry bypass detected
document template bypass detected
Rollback

Set:

TRIMATCH_EXTRA_MODE=off

Then restart the service.

Production note

This runbook does not approve autonomous production rollout.

Production rollout should happen only after staging confirms:

no safety failures
no regression spike
no unexpected shortcut application
no pricing/document/portfolio bypass
no customer-facing copy regression

