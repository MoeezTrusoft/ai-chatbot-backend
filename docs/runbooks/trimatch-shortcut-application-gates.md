
Tri-Match Shortcut Application Gates Runbook
Purpose

This runbook defines the required checks before shortcut application may be implemented.

Current safe state:

shortcut eligibility is computed
shortcut.applied=false
side_effects_allowed=false
Readiness commands

Run these before any implementation branch sets shortcut.applied=true:

uv run python scripts/data/run_trimatch_shortcut_audit_report.py
uv run python scripts/data/run_trimatch_tiebreaker_audit_report.py
uv run python scripts/data/run_trimatch_advisory_audit_report.py
uv run python scripts/data/run_trimatch_shadow_runtime_review.py
uv run python scripts/data/build_trimatch_review_ingestion_audit_report.py
uv run python scripts/data/validate_trimatch_reinforcement.py
uv run python scripts/data/build_trimatch_calibration_report.py
uv run pytest tests/integration/test_trimatch_shortcut_eligibility_audit.py -q
uv run pytest tests/integration/test_trimatch_shortcut_eligibility_evaluator.py -q
uv run pytest tests/integration/test_trimatch_shortcut_audit_report.py -q
uv run pytest tests/integration/test_trimatch_shortcut_candidate_considered.py -q
uv run pytest tests/integration/test_trimatch_shortcut_governance_smoke.py -q
uv run pytest tests/integration/test_trimatch_tiebreaker_application_governance_smoke.py -q
uv run pytest tests/integration/test_trimatch_extra_advisory_mode.py -q
uv run mypy src
Required pass criteria
shortcut audit valid=true
shortcut applied_count=0 before application implementation
shortcut side_effects_allowed_count=0
shortcut sensitive_block_count >= 3
shortcut pricing_sensitive_count >= 1
shortcut document_sensitive_count >= 1
shortcut portfolio_sensitive_count >= 1
tiebreaker audit valid=true
advisory audit valid=true
shadow runtime review valid=true
review ingestion audit valid=true
reinforcement validation valid=true
calibration report valid=true
production_flow_safety_failures=0
shadow_regressions=0
mypy passes
Stop conditions

Do not implement shortcut application if any are true:

pricing-sensitive case could apply
document-sensitive case could apply
portfolio-sensitive case could apply
semantic/fuzzy evidence could apply
shortcut_allowed=false could apply
unreviewed rule could apply
negated evidence could apply
counterfactual evidence could apply
side_effects_allowed could become true
response generation could be bypassed
pricing engine could be bypassed
portfolio registry could be bypassed
NDA/agreement templates could be bypassed
Required next branch
git checkout -b feat/trimatch-shortcut-application-gated
Safety reminder

Shortcut application may only update internal intent.

It must not activate Rules Army v2 globally, bypass deterministic pricing, bypass portfolio registry, bypass NDA/agreement templates, bypass RAG safety, bypass response generation, or invoke tools directly.
