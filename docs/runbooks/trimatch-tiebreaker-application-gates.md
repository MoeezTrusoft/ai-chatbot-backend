
Tri-Match Tiebreaker Application Gates Runbook
Purpose

This runbook defines the required checks before tiebreaker application can be implemented.

Current safe state:

tiebreaker_candidate mode computes eligibility
but keeps decision.applied=false
Readiness commands

Run these before any implementation branch may set decision.applied=true:

uv run python scripts/data/run_trimatch_tiebreaker_audit_report.py
uv run python scripts/data/run_trimatch_advisory_audit_report.py
uv run python scripts/data/run_trimatch_shadow_runtime_review.py
uv run python scripts/data/build_trimatch_review_ingestion_audit_report.py
uv run python scripts/data/validate_trimatch_reinforcement.py
uv run python scripts/data/build_trimatch_calibration_report.py
uv run pytest tests/integration/test_trimatch_tiebreaker_eligibility_evaluator.py -q
uv run pytest tests/integration/test_trimatch_tiebreaker_eligibility_audit.py -q
uv run pytest tests/integration/test_trimatch_tiebreaker_candidate_considered.py -q
uv run pytest tests/integration/test_trimatch_tiebreaker_governance_smoke.py -q
uv run pytest tests/integration/test_trimatch_extra_advisory_mode.py -q
uv run pytest tests/integration/test_trimatch_reinforcement_governance_smoke.py -q
uv run mypy src
Required pass criteria
tiebreaker audit valid=true
tiebreaker applied_count=0 before application implementation
tiebreaker side_effects_allowed_count=0
advisory audit valid=true
shadow runtime review valid=true
review ingestion audit valid=true
reinforcement validation valid=true
calibration report valid=true
production_flow_safety_failures=0
shadow_regressions=0
all tiebreaker tests pass
all advisory tests pass
all reinforcement governance tests pass
mypy passes
Stop conditions

Do not implement or enable tiebreaker application if any are true:

pricing-sensitive case could apply
document-sensitive case could apply
portfolio-sensitive case could apply
negated service could apply
counterfactual service could apply
side_effects_allowed could become true
shortcut_allowed=true appears in an applied rule
final response can be generated outside normal generator
pricing can bypass deterministic quote engine
documents can bypass approved templates
portfolio can bypass registry
Required future implementation branch

Use:

git checkout -b feat/trimatch-tiebreaker-application-gated
Safety reminder

The implementation branch must not enable shortcut mode.

It must not activate Rules Army v2 globally.

It must not change pricing, portfolio, NDA/agreement generation, RAG routing, or response generation directly.
