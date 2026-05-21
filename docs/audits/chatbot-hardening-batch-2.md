# Chatbot Hardening – Batch 2

**Date:** 2026-05-21  
**Engineer:** Claude Code

## Files Inspected

- `src/bookcraft/components/extraction/extractor.py` — consultation extraction missing
- `src/bookcraft/components/extraction/schemas.py` — ConsultationRequestExtraction schema
- `src/bookcraft/components/extraction/state_applier.py` — confidence/correction logic
- `src/bookcraft/components/preprocessor/processor.py` — SERVICE_KEYWORDS contamination
- `src/bookcraft/components/actions/planner.py` — pricing deadline logic, timezone hardcode
- `src/bookcraft/components/actions/slot_resolver.py` — time extraction
- `src/bookcraft/components/response/generator.py` — hardcoded "memoir", ready-to-buy facts
- `src/bookcraft/components/response/quality_gate.py` — preferred_call_time mapping
- `src/bookcraft/services/chat.py` — attachment ordering, attempt_count

## Root Causes Found

| # | Issue | Root Cause |
|---|-------|-----------|
| 1 | Consultation extraction unreliable | No deterministic regex fallback; relies entirely on intent classifier |
| 2 | Service keyword contamination | COVER_DESIGN has "help writing", "only have an idea", "story writing" — same as ghostwriting |
| 3 | Standalone "cover" too broad | Matches "cover everything", "cover the cost" etc. |
| 4 | Correction blocked by uncertainty | No correction-phrase detector to bypass genre_status=uncertain |
| 5 | Equal confidence can't overwrite | `should_apply_delta` requires strictly-greater confidence; USER_CORRECTED path exists but is only triggered when source is explicitly set |
| 6 | State applier crashes on bad paths | `_get_field` raises ValueError; caught only in some callers |
| 7 | Stale historical service selected | Pack builder uses `services_discussed[-1]` without checking current-turn |
| 8 | Attachment runs before service known | `active_service=None` hardcoded; re-enrichment not done after extraction |
| 9 | Attachment parsing errors swallowed | Broad except in processor; no structured error surfacing |
| 10 | quote_attempt_count not incremented | Counter is read but never incremented on MISSING_INFO plans |
| 11 | Deadline always in missing fields | Added unconditionally regardless of whether deadline is known |
| 12 | LLM can invent price/timeline | Quality gate checks for patterns but doesn't distinguish engine-sourced vs LLM-hallucinated |
| 13 | Time extraction loses specificity | `has_time_hint()` just returns bool; full phrase not extracted |
| 14 | Full message used as requested_time_text | `processed.normalized` (entire message) passed as time |
| 15 | Timezone defaults to Chicago | Customer-facing booking uses `default_business_timezone = "America/Chicago"` |
| 16 | Consultation handoff retriggers | No `consultation_handoff_created` state guard |
| 17 | `preferred_call_time` printed literally | Falls through to `f"Could you share more about {key.replace('_', ' ')}?"` in generator context |
| 18 | Hardcoded "memoir" in NDA response | `around the memoir` hardcoded in generator |
| 19 | Ready-to-buy claims unshared facts | Template says "you've shared the manuscript stage and category" unconditionally |

## Files Changed

- `src/bookcraft/components/extraction/extractor.py`
- `src/bookcraft/components/extraction/schemas.py`
- `src/bookcraft/components/extraction/state_applier.py`
- `src/bookcraft/components/preprocessor/processor.py`
- `src/bookcraft/components/actions/planner.py`
- `src/bookcraft/components/actions/slot_resolver.py`
- `src/bookcraft/components/response/generator.py`
- `src/bookcraft/services/chat.py`
- `src/bookcraft/infra/trace_sanitizer.py` (minor — attachment error)

## Tests Added

- `tests/unit/test_hardening_batch2.py`

## Validation Commands

```
uv run ruff check . --fix
uv run ruff format .
uv run mypy src
APP_ENV=test API_AUTH_MODE=off uv run pytest tests/unit/test_hardening_batch2.py -v
```

## Remaining Risks

- **Steps 6/7**: State applier safe path handling is minimal; deep multi-level paths (3+ levels) are still not supported. Requires schema extension if needed.
- **Step 12**: LLM price gate is heuristic-based (pattern matching); a model-level citation requirement would be more robust but is out of scope.
- **Step 15**: Timezone clarification is advisory; booking service must still validate on its side.
- **Step 16**: `consultation_handoff_created` flag is in-memory only; does not persist across sessions.
