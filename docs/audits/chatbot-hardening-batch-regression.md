# Batch 1–3 Regression Audit

**Date:** 2026-05-21
**Engineer:** Claude Code
**Scope:** Cross-batch interaction audit covering safety/privacy gates, lead objective,
consultation objective, service routing, pricing flow, response planner, response generator,
quality gate, formatter, and persistence/action dispatch.

## Interaction Conflicts Investigated

| Domain A | Domain B | Risk |
|----------|----------|------|
| Answer-before-capture | Lead form injection | Form appears during informational answer phase |
| Lead-created state | Consultation objective | Consultation retrigger after lead already created |
| Expired pending action | Action dispatcher | Stale action dispatched after TTL |
| Idempotency key | Concurrent confirmation | Double-dispatch on concurrent turns |
| RAG failure | Response generator | Unsupported policy claims when RAG is empty |
| Roman Urdu bypass | Language guard | PII bypass vs. lead bypass race condition |
| Quality gate blocks | Safe fallback | Fallback asks multiple questions |
| Correction source | State applier | Equal-confidence correction overwrite |
| Privacy complaint + contact | Lead objective | Lead created from complaint context |
| Portfolio rich segments | Quality gate URL check | Raw URLs in portfolio text |

## Test File

`tests/integration/test_batch_regression_suite.py`

## Commands Run

```
uv run ruff check . --fix      → All checks passed
uv run ruff format .           → 467 files unchanged
uv run mypy src                → Success: no issues in 201 files

APP_ENV=test API_AUTH_MODE=off uv run pytest \
  tests/integration/test_batch_regression_suite.py -v
                               → 39 passed, 0 failed (81s)
```

## Defect Found and Fixed During Regression

**`_question_count` false-positive on "word count or page count"**

The multi-slot sentence detector in `quality_gate._question_count` counted
"word count" and "page count" as two distinct slots, causing the response
"What rough word count or page count should I use?" to fail the one-question
rule even though it asks a single count dimension.

**Fix:** Before slot-counting, normalise the pair
`word count or|and page count` → a single `LENGTH` token.
This matches the contact-pair treatment already applied to `email or phone`.

File changed: `src/bookcraft/components/response/quality_gate.py`

---

## Remaining Risks (post-regression)

### HIGH — Multi-worker idempotency
`SalesActionDispatcher._dispatched` is an in-process dict. In a multi-worker
deployment (gunicorn, K8s), two workers can both dispatch the same action
before either marks the key. Requires a Redis SETNX or DB upsert.
**Workaround:** single-process Uvicorn in current deployment.

### HIGH — Expired pending confirmation: persistence copy
`PendingConfirmationState.expires_at` is set on the `ActionPlan` object but
`_apply_sales_action_plan_to_state` must copy it to
`state.sales_actions.pending_confirmation.expires_at`. The in-memory planner
path works (confirmed by regression). The persistence-round-trip path needs
a follow-up integration test to confirm the TTL survives a DB reload.

### MEDIUM — Roman Urdu vocabulary coverage
`_ROMAN_URDU_INTENT_RE` covers ~9 common lead phrases. Novel transliterations
or regional variations fall through to lingua, which may classify them as Hindi
and redirect.
**Mitigation:** Vocabulary is additive — expand in a follow-up pass.

### MEDIUM — Pricing one-question: generator upstream fix needed
The quality gate now blocks multi-slot pricing asks *after* generation.
The generator's `_cta_for_intent` can still produce multi-slot text, which
is then blocked and repaired. Ideally the generator produces single-slot
pricing CTAs natively to avoid the repair cycle.

### LOW — `lead_created_acknowledged` survives restart (one extra confirmation)
If the server restarts mid-confirmation cycle, the flag resets to False and
the bot will re-confirm once. Acceptable for a single re-confirmation.

### LOW — Complaint type routing is hint-only
`_NON_LEAD_CONTEXT_RE` suppresses lead creation in complaint context but the
response planner does not set a `complaint_recovery` primary goal. A dedicated
complaint-type field in `ResponsePlan` would enable richer category-specific
Claude guidance.

### LOW — `answer_before_capture_decision` null-safety
The ABC suppression check relies on the decision object being non-None in the
normal chat turn path (always true). Added defensive guard in chat.py to
prevent a hypothetical edge-case where it could be None.
