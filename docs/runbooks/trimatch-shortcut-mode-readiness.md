
Tri-Match Shortcut Mode Readiness Runbook
Purpose

This runbook defines the required checks before shortcut mode can be implemented.

Shortcut mode is not active today.

Current safe modes
TRIMATCH_EXTRA_MODE=off
TRIMATCH_EXTRA_MODE=shadow
TRIMATCH_EXTRA_MODE=advisory
TRIMATCH_EXTRA_MODE=tiebreaker_candidate

Do not use:

TRIMATCH_EXTRA_MODE=shortcut_candidate

until design, governance tests, audit tooling, and implementation are approved.

Readiness commands

Run these before shortcut implementation work:

uv run python scripts/data/run_trimatch_tiebreaker_audit_report.py
uv run python scripts/data/run_trimatch_advisory_audit_report.py
uv run python scripts/data/run_trimatch_shadow_runtime_review.py
uv run python scripts/data/build_trimatch_review_ingestion_audit_report.py
uv run python scripts/data/validate_trimatch_reinforcement.py
uv run python scripts/data/build_trimatch_calibration_report.py
uv run pytest tests/integration/test_trimatch_tiebreaker_application_governance_smoke.py -q
uv run pytest tests/integration/test_trimatch_tiebreaker_application_gated.py -q
uv run pytest tests/integration/test_trimatch_tiebreaker_eligibility_evaluator.py -q
uv run pytest tests/integration/test_trimatch_tiebreaker_eligibility_audit.py -q
uv run pytest tests/integration/test_trimatch_tiebreaker_candidate_considered.py -q
uv run pytest tests/integration/test_trimatch_tiebreaker_governance_smoke.py -q
uv run pytest tests/integration/test_trimatch_extra_advisory_mode.py -q
uv run pytest tests/integration/test_trimatch_reinforcement_governance_smoke.py -q
uv run mypy src
Required pass criteria
tiebreaker audit valid=true
advisory audit valid=true
shadow runtime review valid=true
review ingestion audit valid=true
reinforcement validation valid=true
calibration report valid=true
production_flow_safety_failures=0
shadow_regressions=0
all tiebreaker governance tests pass
all advisory tests pass
all reinforcement governance tests pass
mypy passes
Stop conditions

Do not implement shortcut mode if:

pricing-sensitive case could shortcut
document-sensitive case could shortcut
portfolio-sensitive case could shortcut
negated service could shortcut
counterfactual service could shortcut
semantic-only rule could shortcut
fuzzy-only rule could shortcut
shortcut_allowed=false could shortcut
unreviewed candidate could shortcut
side_effects_allowed could become true
Required future branch order
docs/trimatch-shortcut-mode-design
→ test/trimatch-shortcut-governance-smoke
→ feat/trimatch-shortcut-candidate-considered
→ feat/trimatch-shortcut-audit-report
→ feat/trimatch-shortcut-application-gated
Safety reminder

Shortcut mode must not:

activate Rules Army v2 globally
bypass deterministic pricing
bypass portfolio registry
bypass NDA/agreement templates
bypass response generation
bypass RAG safety
enable high-stakes document actions

