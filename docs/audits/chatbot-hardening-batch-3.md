# Chatbot Hardening – Batch 3

**Date:** 2026-05-21
**Engineer:** Claude Code

## Files Inspected

- `src/bookcraft/components/leads/objective.py` — aggressive lead capture, no explicit-intent guard, lead_created loops
- `src/bookcraft/services/chat.py` — lead_intake_form injection, RAG status, no rag_status trace field
- `src/bookcraft/components/response/quality_gate.py` — naive question count, weak wrong-service, broad SUCCESS_CLAIM_RE
- `src/bookcraft/components/language_guard/guard.py` — hard redirect on non-English, no Roman Urdu detection
- `src/bookcraft/components/safety/input_guard.py` — frustration vs abuse distinction (mostly OK but needs complement)

## Root Causes Found

| # | Issue | Root Cause |
|---|-------|-----------|
| 1 | Lead capture too aggressive | `_TIMELINE_OR_PRICE_HINT_RE` matches "process," "example," "portfolio" → premature contact ask |
| 2 | No explicit lead intent guard | Contact info alone triggers `create_lead`; no buying-signal check |
| 3 | Lead form appears during answer-before-capture | No check for `answer_before_capture_decision.suppress_contact_until_answered` |
| 4 | Lead-created dominates future turns | `lead_created → "no_change"` loop; no `lead_created_acknowledged` flag |
| 5 | Pricing asks too many slots at once | `_cta_for_intent` joins all missing fields with commas |
| 6 | Complaint too narrow | Only `guarantee_pressure` handled; no complaint-type routing |
| 7 | Safe fallback lists multiple questions | `_build_safe_fallback` can produce multi-slot question string |
| 8 | Portfolio text may have raw URLs | Already fixed PR 3; need quality gate test coverage |
| 9 | Portfolio follow-up service-unaware | `_cta_for_intent` has hardcoded generic portfolio phrase |
| 10 | Question counting counts `?` | `_question_count` returns `text.count("?")` — multi-slot asks with one `?` pass |
| 11 | Missing-next-step too weak | `_PROGRESSION_RE` matches "tell me more," "share," etc. as valid next steps |
| 12 | Wrong-service guard too narrow | Only checks ghostwriting when cover_design active |
| 13 | Blocked-action success too broad | `_SUCCESS_CLAIM_RE` matches "ready," "done," "completed" broadly |
| 14 | Roman Urdu lost | Language guard hard-redirects; no Roman Urdu vocabulary detection |
| 15 | Frustrated profanity triggers escalation | Already has casual profanity → warn; need complaint recovery path |
| 16 | RAG failures traceable but `rag_status` not in trace dict | No `rag_status` field in live trace |

## Files Changed

- `src/bookcraft/components/leads/objective.py`
- `src/bookcraft/services/chat.py`
- `src/bookcraft/components/response/quality_gate.py`
- `src/bookcraft/components/language_guard/guard.py`
- `src/bookcraft/domain/state.py`

## Tests Added

- `tests/unit/test_hardening_batch3.py`

## Validation Commands

```
uv run ruff check . --fix
uv run ruff format .
uv run mypy src
APP_ENV=test API_AUTH_MODE=off uv run pytest tests/unit/test_hardening_batch3.py -v
```

## Remaining Risks

- **Step 6** (complaint categories): Claude still writes final text; the categories feed into the response plan as hints — they do not force specific prose.
- **Step 14** (Roman Urdu): Detection is vocabulary-based; may miss novel transliterations.
- **Step 10** (multi-slot question count): Heuristic detection; will not catch all creative phrasings.
