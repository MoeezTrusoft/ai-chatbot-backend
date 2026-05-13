# Tri-Match Shortcut Eligibility Evaluator Runbook

## Purpose

This phase computes shortcut eligibility while keeping shortcut application disabled.

It may set:

```json
{
  "shortcut": {
    "eligible": true
  }
}
But it must always keep:

{
  "shortcut": {
    "applied": false
  },
  "safety": {
    "side_effects_allowed": false
  }
}
Command
uv run pytest tests/integration/test_trimatch_shortcut_eligibility_evaluator.py -q
Safety

This phase does not apply shortcuts.

It does not:

override final intent
enable direct shortcut routing
change extraction/state
change pricing
change portfolio
generate NDA or agreement text
change RAG routing
change response generation
