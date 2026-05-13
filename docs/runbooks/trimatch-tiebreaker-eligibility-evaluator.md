# Tri-Match Tiebreaker Eligibility Evaluator Runbook

## Purpose

This phase computes tiebreaker eligibility reasons while keeping tiebreaker application disabled.

It may set:

```json
{
  "decision": {
    "eligible": true
  }
}
But it must always keep:

{
  "decision": {
    "applied": false
  },
  "safety": {
    "side_effects_allowed": false
  }
}
Command
uv run pytest tests/integration/test_trimatch_tiebreaker_eligibility_evaluator.py -q
Safety

This phase does not apply tiebreakers.

It does not:

override final intent
enable shortcut behavior
change extraction/state
change pricing
change portfolio
generate NDA or agreement text
change RAG routing
change response generation
Next phase

A future branch may implement tightly gated tiebreaker application, but only after audit reports and governance tests are updated to prove no pricing/document/portfolio/negation/counterfactual bypass is possible.
