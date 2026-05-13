# Tri-Match Tiebreaker Eligibility Audit Runbook

## Purpose

This report update audits the tiebreaker eligibility evaluator.

It verifies:

- `decision.eligible` can be counted
- `decision.applied` remains `false`
- `safety.side_effects_allowed` remains `false`
- blocked reasons are summarized
- sensitive pricing/document/portfolio cases remain blocked

## Command

```bash
uv run python scripts/data/run_trimatch_tiebreaker_audit_report.py
Focused regression test:

uv run pytest tests/integration/test_trimatch_tiebreaker_eligibility_audit.py -q
Safety

This branch does not apply tiebreakers.

It only improves reporting around eligibility and blocked reasons.
