# Tri-Match Tiebreaker Candidate Considered Runbook

## Purpose

This phase implements `TRIMATCH_EXTRA_MODE=tiebreaker_candidate` as a consideration-only mode.

It logs:

```text
trimatch.extra_tiebreaker_considered
It does not apply a tiebreaker.

Guarantees

The event must always show:

{
  "decision": {
    "applied": false
  },
  "safety": {
    "side_effects_allowed": false
  }
}
Command
uv run pytest tests/integration/test_trimatch_tiebreaker_candidate_considered.py -q
Safety

This phase does not:

override final intent
enable shortcut behavior
change extraction/state
change pricing
change portfolio
generate NDA or agreement text
change RAG routing
change response generation
Next phase

A future branch may add eligibility calculations, but applied must remain false until separate tiebreaker-application governance is approved.
