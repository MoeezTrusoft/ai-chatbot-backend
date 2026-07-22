# Tri-Match Rules Army v2 — Promotion Status

**Updated:** 2026-07-22 · **State:** CALIBRATED & PROMOTED on branch `trimatch-v2-promotion` (NOT yet merged to main/deployment) · **Runtime impact when merged:** none until `trimatch_mode` is flipped off shadow.

## What was done

The v2 army now **passes `TriMatchVerifier`** (and `make trimatch-verify`) against the full 374-example core eval. Calibrated by three parallel per-dimension passes against `scripts/dev/trimatch_dimension_check.py`:

| Dimension | Before → After (pattern recall) | Other fixes |
|---|---|---|
| service_intent | 0.230 → 0.978 | +71 discriminative pattern rules |
| query_intent | 0.235 → 0.944 | +100 pattern rules |
| funnel_stage | 0.260 → 0.818 | 6 structural (pricing-in-funnel) fixed; exact precision 0.932 → 1.000; +34 patterns |

All precision layers ≥0.97; all recall floors met; 0 structural errors. Patterns are discriminative token-subsequences (contiguous n-gram match, no lemmatization), authored to avoid cross-label collisions — precision held at ~1.0 on the pattern layer.

## Promotion changes (on the branch)

- Active rules replaced: `data/trimatch/rules/{service_intent,query_intent,funnel_stage}_rules.v2.json` (v1 removed).
- Active eval: v2 core eval added alongside v1.
- **Semantic layer disabled** (74 rules, `enabled=false`): the semantic matcher bypasses negation/hedge/counterfactual suppression (its match span is the example string, so `dampen_confidence`'s `find()` returns -1 and never flags negation). It is the lowest-precision layer (0.52–0.68) and is NOT gated by the verifier; the audited exact/regex/pattern layers now carry the recall. Re-enable only after the negation-aware preprocessor upgrades below.
- Tiebreaker audit fixture updated (`run_trimatch_tiebreaker_audit_report.py`): Case 1 now uses a neutral base so its synthetic disagreement survives v2's stronger base classification.

## Still deferred (not required to pass the verifier)

- The **advanced context eval** (`eval_advanced/`, 8 examples) and `_context_rules.v1.json` need the preprocessor upgrades the README lists: terminator-aware negation, backward negation scope, ordered service atoms, counterfactual tagging. Only after those should the semantic layer be re-enabled.
- v2 sidecars (`_negation_cues.v2`, `_compound_word_variants.v2`) are NOT wired into active `data/trimatch/sidecars/` — the verifier passes without them; wire as part of the preprocessor-upgrade work.

## Safe rollout (unchanged)

Tri-Match is shadow-mode in prod, so merging this changes only shadow votes/logs. Keep `trimatch_mode=shadow`, run the 50-message production flow (mock + live), then consider `vote_only` → `shortcut_enabled`.
