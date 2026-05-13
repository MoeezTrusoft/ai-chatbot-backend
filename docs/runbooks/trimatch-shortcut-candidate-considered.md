# Tri-Match Shortcut Candidate Considered Runbook

## Purpose

This phase implements `TRIMATCH_EXTRA_MODE=shortcut_candidate` as consideration-only.

It logs:

```text
trimatch.extra_shortcut_considered
It does not apply a shortcut.

Guarantees

The event must always show:

{
  "shortcut": {
    "eligible": false,
    "applied": false
  },
  "safety": {
    "side_effects_allowed": false
  }
}
Command
uv run pytest tests/integration/test_trimatch_shortcut_candidate_considered.py -q
Safety

This phase does not:

override final intent
enable shortcut application
change extraction/state
change pricing
change portfolio
generate NDA or agreement text
change RAG routing
change response generation
