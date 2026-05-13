# Tri-Match Runtime Shadow Loader Runbook

## Purpose

The runtime shadow loader allows approved/staged extra Tri-Match RulePacks to run beside the active production rule pack.

It does **not** affect the final user response.

## Settings

```env
TRIMATCH_EXTRA_MODE=off
TRIMATCH_EXTRA_RULE_DIR=data/trimatch/reinforcement/staged_from_reviews
TRIMATCH_EXTRA_FUZZY_ENABLED=false
Allowed modes:

off
shadow
Behavior

When TRIMATCH_EXTRA_MODE=shadow:

Active Tri-Match still runs normally.
Extra staged RulePack runs separately.
Extra result is logged as trimatch.extra_shadow_voted.
The extra result is not passed into the ensemble classifier.
The final response is unchanged.

If the extra engine fails, the chat service logs trimatch.extra_shadow_failed and continues.

Safety

This is the first runtime bridge for human-approved reinforcement rules.

It must remain shadow-only until calibration proves safety.

Promotion path:

compiled staged RulePack
→ runtime shadow
→ disagreement logging
→ calibration
→ advisory
→ tiebreaker
→ shortcut candidate

