# Batch 4 — Production Readiness Audit

**Date:** 2026-05-21
**Scope:** Close remaining production gaps after Batches 1–3 and the consultation state-contract hotfix.

## Root Causes Addressed

### 1. In-process idempotency (CRITICAL)
`SalesActionDispatcher._dispatched` is a `dict[str, bool]` that only lives in a single Python process.
Two Gunicorn workers, two containers, or a server restart can all dispatch the same action concurrently.

**Fix:** Add `SalesActionRecord` DB model with `UNIQUE(idempotency_key)`.
Create `ActionIdempotencyRepository` backed by DB.
Wire as optional dep into `SalesActionDispatcher`; fall back to in-process in test mode.

### 2. Pending confirmation expiry not persisted (HIGH)
`SalesActionPlanner.plan()` computes `ActionPlan.pending_expires_at` via `make_pending_expires_at()`.
`_apply_sales_action_plan_to_state()` in `chat.py` writes `.type`, `.payload`, `.created_at` to
`state.sales_actions.pending_confirmation` but **does not write `.expires_at`**.

Result: DB round-trip loses expiry → stale pending confirmation can be confirmed hours later.

**Fix:** Copy `action_plan.pending_expires_at` → `state.sales_actions.pending_confirmation.expires_at` in `_apply_sales_action_plan_to_state()`.

### 3. Pricing CTA asks multiple slots (MEDIUM)
`_cta_for_intent()` in `generator.py` joins all missing pricing fields into a single sentence.
The quality gate must then repair the multi-slot output.

**Fix:** Priority-ordered single-slot pricing CTA in `_pricing_single_question_cta()`.

### 4. Complaint recovery is hint-only (MEDIUM)
The bot avoids lead capture in complaint context but doesn't enter a strong recovery posture.
No `ComplaintClassifier` or dedicated `complaint_recovery` planner goal.

**Fix:** `src/bookcraft/components/complaints/classifier.py` wired into `chat.py` + planner.

### 5. Pre-existing test failures undocumented (LOW)
10 failures inherited from earlier sessions; none introduced by Batches 1–4.
See `docs/audits/pre-existing-test-failures.md`.

## Files Changed

1. `src/bookcraft/components/storage/models.py` — `SalesActionRecord` model
2. `migrations/versions/20260521_0003_sales_action_idempotency.py` — Alembic migration
3. `src/bookcraft/components/storage/action_idempotency_repository.py` — NEW
4. `src/bookcraft/components/actions/dispatcher.py` — wire durable idempotency
5. `src/bookcraft/services/chat.py` — copy `pending_expires_at` to state
6. `src/bookcraft/components/response/generator.py` — single-slot pricing CTA
7. `src/bookcraft/components/complaints/classifier.py` — NEW
8. `src/bookcraft/components/complaints/__init__.py` — NEW
9. `src/bookcraft/services/chat.py` — wire complaint classifier
10. `src/bookcraft/components/response/planner.py` — complaint_recovery goal

## Tests Added

- `tests/unit/test_hardening_batch4.py`
- `tests/integration/test_pending_confirmation_expiry_roundtrip.py`

## Remaining Risks (post-Batch 4)

### HIGH — Complaint recovery requires LLM for nuanced classification
The regex-based `ComplaintClassifier` handles clear patterns but may miss subtle frustration.
A lightweight Claude call via structured output would be more robust.
**Mitigation:** Classifier is additive; missed cases fall through to existing safety guard.

### MEDIUM — Roman Urdu vocabulary coverage
Still limited to ~9 lead phrases.

### LOW — `lead_created_acknowledged` survives restart (one extra confirmation)
Acceptable for single re-confirmation.
