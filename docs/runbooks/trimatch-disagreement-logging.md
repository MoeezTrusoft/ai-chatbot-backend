# Tri-Match Disagreement Logging Runbook

## Purpose

Disagreement logging records differences and shadow observations between:

- active Tri-Match
- extra shadow Tri-Match
- ensemble intent
- final hardened intent

This creates the evidence stream needed for calibration and human-approved reinforcement.

## Event

```text
trimatch.disagreement_observed
Logged payload

The event stores snapshots for:

active_trimatch
extra_shadow
ensemble
final

and includes:

disagreements
should_log

The event is emitted when:

A source disagrees with the final hardened intent, or
The extra shadow RulePack produces evidence.
Safety

This event is observational only.

It does not change intent classification, tool routing, response generation, pricing, portfolio, NDA, or agreement behavior.

Use in reinforcement

Disagreements and shadow observations should feed:

disagreement/shadow event
→ candidate miner / review queue
→ human review
→ staged RulePack
→ shadow
→ calibration

