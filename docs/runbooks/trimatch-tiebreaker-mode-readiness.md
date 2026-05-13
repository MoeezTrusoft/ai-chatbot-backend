
Tri-Match Tiebreaker Mode Readiness Runbook
Purpose

This runbook defines checks required before implementing or enabling Tri-Match tiebreaker candidate mode.

Tiebreaker mode is not active today.

Current safe modes
TRIMATCH_EXTRA_MODE=off
TRIMATCH_EXTRA_MODE=shadow
TRIMATCH_EXTRA_MODE=advisory

Do not use:

TRIMATCH_EXTRA_MODE=tiebreaker_candidate

until design, tests, and implementation are approved.

Readiness commands
uv run python scripts/data/run_trimatch_shadow_runtime_review.py
uv run python scripts/data/run_trimatch_advisory_audit_report.py
uv run python scripts/data/build_trimatch_review_ingestion_audit_report.py
uv run python scripts/data/validate_trimatch_reinforcement.py
uv run python scripts/data/compile_approved_trimatch_candidates.py --version approved_candidates.tiebreaker_readiness.v1 --output reports/trimatch/approved_candidates.tiebreaker_readiness.rulepack.json
uv run python scripts/data/build_trimatch_calibration_report.py
uv run pytest tests/integration/test_trimatch_reinforcement_governance_smoke.py -q
uv run pytest tests/integration/test_trimatch_extra_advisory_mode.py -q
uv run pytest tests/integration/test_trimatch_runtime_shadow_loader.py tests/integration/test_trimatch_disagreement_logging.py -q
uv run mypy src
Required pass criteria
shadow runtime review valid=true
advisory audit valid=true
review ingestion audit valid=true
reinforcement validation valid=true
calibration report valid=true
production-flow safety failures=0
shadow regressions=0
governance smoke tests pass
advisory tests pass
runtime shadow tests pass
disagreement logging tests pass
Stop conditions

Do not implement or enable tiebreaker mode if:

shadow runtime review has failed turns
advisory audit has failed turns
calibration report shows regressions
production-flow safety failures are non-zero
review ingestion audit is invalid
manual review coverage is too low
pricing/document/portfolio safety tests are missing
negation/counterfactual tests are missing
Safety rule

Tiebreaker mode must never influence:

pricing values
timeline values
discounts
payment plans
NDA text
agreement text
agreement fee fields
portfolio/sample URLs
RAG routing
response text directly
shortcut classification
Rollback

Future tiebreaker mode must be disabled with:

TRIMATCH_EXTRA_MODE=off

