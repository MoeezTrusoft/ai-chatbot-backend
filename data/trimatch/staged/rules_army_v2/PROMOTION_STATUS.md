# Tri-Match Rules Army v2 — Promotion Status

**Updated:** 2026-07-22 · **State:** STAGED, NOT promoted · **Runtime impact if promoted:** none today (Tri-Match runs in **shadow mode** in prod — see config `trimatch_mode`).

## Why not promoted yet

The staged v2 army (947 rules) does **not** pass `TriMatchVerifier` against its own core eval (374 examples). Promoting it would either require overfitting rules to the eval (defeats the gate) or lowering the floors (games the gate) — neither is acceptable. This is a real calibration effort, matching the army README's own guidance ("promote high-precision rules only after calibration; humans approve").

## Measured gap (v2 army vs `data/trimatch/eval/*.v2` core eval, 2026-07-22)

10 verifier errors:

**Structural (6)** — funnel-stage rules must not encode pricing/legal terms (`price`/`cost`/`contract text`/`legal clause`):
`FUNNEL-QUOTE-EX-009` ("cost estimate"), `FUNNEL-QUOTE-RX-011`, `FUNNEL-QUOTE-PT-014`, `FUNNEL-NEG-EX-008` ("lower the price"), `FUNNEL-NEG-RX-011`, `FUNNEL-NEG-PT-014`.
→ Fix: move pricing/negotiation detection out of the funnel-stage dimension (it belongs to service/pricing intent), or reword to non-pricing funnel cues ("quote", "estimate", "discount", "negotiate" without the literal `price`/`cost` tokens).

**Precision floor 0.97 (1):** `funnel_stage:exact` = **0.932** (a few exact funnel rules misfire).

**Recall floor 0.45 for the pattern layer (3):**
`funnel_stage:pattern` = **0.260**, `query_intent:pattern` = **0.235**, `service_intent:pattern` = **0.230**.
This is the hard one — pattern-layer recall is roughly half the floor across all three dimensions, i.e. the pattern rules cover far too little. Closing it needs genuine new pattern coverage (not fitted to these 374 held-out examples) and, per the README/MANIFEST, the preprocessor upgrades the advanced eval depends on (terminator-aware negation, backward negation scope, ordered service atoms, counterfactual tagging).

## Recommended promotion path (unchanged from README)

1. Fix the 6 structural funnel/pricing violations.
2. Land the preprocessor upgrades the advanced eval requires.
3. Raise pattern-layer recall to floor **with held-out validation** (expand the eval; do not fit to the current 374).
4. Re-run `evaluate_trimatch_rules_army_v2.py` / `validate_trimatch_rules_army_v2.py`.
5. Copy the passing pack to `data/trimatch/rules/` and keep `trimatch_mode=shadow`; run the 50-message production flow (mock + live).
6. Only then consider `vote_only` / `shortcut_enabled`.

## Note on the active gate

The active verifier (`make trimatch-verify` and `test_trimatch_verifier_accepts_seed_rules_and_eval`) grades the **active v1 seed rules against v1 seed eval** and passes. The v2 core eval was previously mis-copied into `data/trimatch/eval/` (byte-identical duplicate of this army's eval), which broke the active gate by grading v1 rules against v2-army eval; those duplicates were removed (the originals live here under `eval/`).
