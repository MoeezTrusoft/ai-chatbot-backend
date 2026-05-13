
Tri-Match Advisory Mode Readiness Runbook
Purpose

This runbook defines the checks that must pass before advisory mode can be implemented or enabled.

Advisory mode is implemented as a guarded logging-only mode, but must not be enabled outside controlled review until the readiness checks pass.

Current allowed mode
TRIMATCH_EXTRA_MODE=shadow

Do not use:

TRIMATCH_EXTRA_MODE=advisory

until the design and implementation are approved.

Readiness checklist

Run these before any advisory-mode implementation PR:

uv run python scripts/data/run_trimatch_shadow_runtime_review.py
uv run python scripts/data/build_trimatch_review_ingestion_audit_report.py
uv run python scripts/data/validate_trimatch_reinforcement.py
uv run python scripts/data/compile_approved_trimatch_candidates.py --version approved_candidates.advisory_readiness.v1 --output reports/trimatch/approved_candidates.advisory_readiness.rulepack.json
uv run python scripts/data/build_trimatch_calibration_report.py
uv run pytest tests/integration/test_trimatch_reinforcement_governance_smoke.py -q
uv run pytest tests/integration/test_trimatch_disagreement_logging.py tests/integration/test_trimatch_runtime_shadow_loader.py -q
uv run mypy src
Required pass criteria
shadow runtime review valid=true
failed_turns=0
reinforcement validation valid=true
review ingestion audit valid=true
calibration report valid=true
production_flow_safety_failures=0
shadow_regressions=0
governance smoke tests pass
runtime shadow loader tests pass
disagreement logging tests pass
Advisory mode must remain non-routing

Advisory mode may log:

trimatch.extra_advisory_recommended

Advisory mode must not affect:

final intent
state extraction
pricing
timeline
portfolio
NDA generation
agreement generation
RAG routing
response generation
Stop conditions

Do not implement or enable advisory mode if any of these are true:

shadow runtime review has failed turns
calibration report shows regressions
review ingestion audit is invalid
production-flow safety failures are non-zero
governance smoke test fails
manual review coverage is too low
negation/counterfactual examples are missing
pricing/document/portfolio safety cases are missing
Rollback

Future advisory mode must be disabled with:

TRIMATCH_EXTRA_MODE=off

No database migration or deploy rollback should be required to disable advisory behavior.
