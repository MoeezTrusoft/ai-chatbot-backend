# Tri-Match Governance Final Readiness

## Status

Final governance documentation.

The Tri-Match promotion ladder is now implemented through gated shortcut application with governance smoke coverage.

## Completed ladder

```text
shadow review
candidate mining
human review tools
review template ingestion
review ingestion audit
reinforcement governance smoke
advisory mode design
advisory mode implementation
advisory audit report
tiebreaker mode design
tiebreaker governance smoke
tiebreaker considered mode
tiebreaker audit report
tiebreaker eligibility evaluator
tiebreaker eligibility audit
tiebreaker application gates
tiebreaker gated application
tiebreaker application governance smoke
shortcut mode design
shortcut governance smoke
shortcut considered mode
shortcut audit report
shortcut eligibility evaluator
shortcut eligibility audit
shortcut application gates
shortcut gated application
shortcut application governance smoke
Runtime modes
TRIMATCH_EXTRA_MODE=off
TRIMATCH_EXTRA_MODE=shadow
TRIMATCH_EXTRA_MODE=advisory
TRIMATCH_EXTRA_MODE=tiebreaker_candidate
TRIMATCH_EXTRA_MODE=shortcut_candidate
Production safety defaults

Recommended production default:

TRIMATCH_EXTRA_MODE=off

Recommended staged rollout order:

off
shadow
advisory
tiebreaker_candidate
shortcut_candidate

Do not skip stages.

Safety invariants

These must remain true:

side_effects_allowed=false
pricing engine owns pricing
portfolio registry owns portfolio URLs
document templates own NDA/agreement generation
RAG retriever owns retrieval
response generator owns final response text
tools are never invoked directly by Tri-Match shortcut logic
Rules Army v2 is not globally activated
Required recurring checks
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
Required report pass criteria
shortcut audit valid=true
shortcut side_effects_allowed_count=0
shortcut sensitive_block_count >= 3
tiebreaker audit valid=true
tiebreaker side_effects_allowed_count=0
advisory audit valid=true
shadow runtime review valid=true
review ingestion audit valid=true
reinforcement validation valid=true
calibration report valid=true
production_flow_safety_failures=0
shadow_regressions=0
mypy passes
Rollback

Immediate rollback:

TRIMATCH_EXTRA_MODE=off

Rollback must not require:

database migration
code rollback
event deletion
rule deletion
manual report cleanup
Remaining operational work

Before real production enablement:

add CI job for governance reports
add staging rollout checklist
add production config checklist
add alerting for shortcut/tiebreaker side_effects_allowed_count > 0
add alerting for pricing/document/portfolio shortcut application
add release note for Tri-Match governance ladder

