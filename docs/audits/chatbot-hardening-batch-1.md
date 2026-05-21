# Chatbot Hardening – Batch 1

**Date:** 2026-05-21  
**Engineer:** Claude Code  

## Files Inspected

- `src/bookcraft/services/chat.py` — orchestration pipeline, final response path, trace recording, event append
- `src/bookcraft/components/response/quality_gate.py` — quality checks, safe_fallback
- `src/bookcraft/components/response/generator.py` — draft/repair generation
- `src/bookcraft/components/actions/planner.py` — pending confirmation handling, action planning
- `src/bookcraft/components/actions/dispatcher.py` — side-effect dispatch, no idempotency
- `src/bookcraft/components/actions/slot_resolver.py` — `is_confirmation_text()`, `YES_CONFIRMATIONS`
- `src/bookcraft/components/storage/thread_repository.py` — `append_event`, `save_state`, version conflict
- `src/bookcraft/components/storage/state_sanitizer.py` — redaction logic
- `src/bookcraft/domain/state.py` — `PendingConfirmationState` (has `created_at`, `expires_at`, but no TTL logic)

## Root Causes Found

| # | Issue | Root Cause |
|---|-------|-----------|
| 1 | Unsafe draft sent on repair fail | In production, `deterministic_final_text_blocked=True` but `final_draft = draft` (the original blocked draft) is still used |
| 2 | Event payloads store raw user messages | `user.message` events are appended with `payload={"text": payload.message}` — `redact_mapping` is applied but the raw message field key is still `"text"` with the full message |
| 3 | Live traces contain raw contact PII | `contact_capture.model_dump()` emits `name`, `email`, `phone` verbatim; `lead_intake_payload` may also contain raw PII |
| 4 | Pending confirmations have no expiry enforcement | `PendingConfirmationState.expires_at` exists in the model but is never set or checked |
| 5 | Broad confirmations can fire wrong action | `is_confirmation_text()` doesn't consider pending action type; "schedule it" can confirm NDA |
| 6 | No action idempotency | Dispatcher has no idempotency keys; retry/double-confirm can double-dispatch |
| 7 | Side effects before durable persistence | `action_result = await dispatcher.dispatch(...)` happens at line ~643; `save_state` happens at line ~1090 |
| 8 | No per-thread concurrency lock | `ThreadVersionConflictError` is raised but not retried; concurrent confirmations can both read same version |
| 9 | Approved URLs from original draft used | `formatter.format(final_text, approved_urls=set(draft.approved_urls))` — uses `draft.approved_urls` even when `final_draft != draft` |

## Files Changed

- `src/bookcraft/components/actions/slot_resolver.py` — action-specific confirmation, `is_pending_expired()`
- `src/bookcraft/components/actions/planner.py` — set `expires_at` on pending confirmations, check expiry
- `src/bookcraft/components/actions/dispatcher.py` — idempotency keys, record-before-dispatch hook
- `src/bookcraft/services/chat.py` — fail-closed final response, trace PII masking, event sanitization, approved_urls fix
- `src/bookcraft/infra/trace_sanitizer.py` — **NEW** trace-safe serializer for contact/lead PII

## Tests Added

- `tests/unit/test_hardening_batch1.py` — unit tests for all 9 fixes

## Validation Commands

```
uv run ruff check . --fix
uv run ruff format .
uv run mypy src
APP_ENV=test API_AUTH_MODE=off uv run pytest tests/unit/test_hardening_batch1.py -v
```

## Remaining Risks

- **Concurrency** (Step 7): Version conflict is raised and _not_ retried in-process. A true distributed lock (Redis SETNX, Postgres advisory lock) is needed for multi-worker safety. Documented below in code with `TODO: distributed-lock`.
- **Outbox pattern** (Step 6): Full outbox with separate worker is not implemented. Idempotency keys prevent double-dispatch on retry but do not guarantee at-least-once delivery.
- **Expiry TTL config**: TTL values are hardcoded constants; consider making them configurable.
