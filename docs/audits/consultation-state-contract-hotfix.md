# Consultation State-Contract Hotfix

**Date:** 2026-05-21  
**Severity:** CRITICAL — Production state-contract bug

## Root Cause Summary

The chatbot loses contact state between turns because **`contact_slots()` in
`slot_resolver.py` does not read from `state.contact_info`** — the canonical
source where `contact_capture.merge_with_state()` writes merged contact data.

On the turn where contact is shared ("Maya Author maya@example.com +1 555 987 6543"):
- `contact_capture` correctly extracts name/email/phone into `ContactCaptureResult`
- `state.contact_info` is updated with these values
- **BUT** `state.personal.name/email/phone` (FieldMeta) are NOT synced

On the following turns:
- `contact_slots()` reads from `extraction.contact.full_name`, `state.personal.*`,
  and runtime atoms — all of which are **empty for a returning user**
- `contact_slots()` does NOT read from `state.contact_info`
- The action planner concludes contact is missing and asks for it again

Secondary bugs:
1. `state.sales_actions.consultation.preferred_time_window` is set when
   `_apply_sales_action_plan_to_state` runs, but ONLY if an action plan of type
   `SCHEDULE_CONSULTATION` is created. If the plan is `MISSING_INFO` (because
   contact was wrongly empty), the preferred time is never persisted there.
2. `state.preferred_call_time` (top-level) IS set by consultation_objective if
   call time is extracted. But on the next turn (consultation status question),
   the response planner doesn't know to use it as evidence.
3. `"full draft"` is not in `_COMPLETED_PHRASES` → manuscript status not extracted.
4. No "consultation status question" detector → bot falls through to generic flow.
5. Quality gate doesn't validate that scheduling claims are backed by state.

## Current Contact Truth Sources

| Source | Written | Read |
|--------|---------|------|
| `state.contact_info` | chat.py after `merge_with_state()` | quality gate check 21, contact_is_ready() |
| `state.personal.name/email/phone` | state_applier (extraction deltas only) | `contact_slots()`, `has_email_or_phone()` |
| `state.sales_actions.lead.name/email/phone` | `_apply_sales_action_result_to_state` (after lead action success) | `contact_slots()` (missing!) |
| `extraction.contact.full_name/email/phone` | CombinedExtractor | `contact_slots()` |
| Runtime atoms (emails/phones) | Preprocessor | `contact_slots()` |

**Gap:** `state.contact_info` is never read by `contact_slots()`.

## Current Consultation Truth Sources

| Source | Written | Read |
|--------|---------|------|
| `state.preferred_call_time` | consultation_objective (extracted_preferred_call_time) | context pack |
| `state.consultation_stage` | chat.py after consultation decision | context pack |
| `state.sales_actions.consultation.preferred_time_window` | `_apply_sales_action_plan_to_state` | planner |
| `state.sales_actions.consultation.confirmed_appointment_id` | `_apply_sales_action_result_to_state` | planner |
| `state.sales_actions.consultation.requested` | `_apply_sales_action_plan_to_state` | planner |
| `state.consultation_handoff_created` | `_apply_sales_action_result_to_state` | consultation engine |

**Gap:** No detector for "have my consultation been scheduled?" questions.

## Files Changed

1. `src/bookcraft/components/actions/slot_resolver.py` — Phase 3: read from all contact sources
2. `src/bookcraft/services/chat.py` — Phase 4: sync contact_capture to personal + lead state
3. `src/bookcraft/components/preprocessor/detectors/manuscript_status_detector.py` — Phase 9
4. `src/bookcraft/components/leads/contact_recovery.py` — Phase 8: consultation status detector
5. `src/bookcraft/components/sales/consultation_state.py` — Phase 5: canonical state reducer
6. `src/bookcraft/components/response/quality_gate.py` — Phase 7: scheduling claim check
7. `src/bookcraft/components/extraction/llm_extractor.py` — Phase 10: LLM extractor interface

## Tests Added

`tests/unit/test_consultation_state_hotfix.py`
`tests/integration/test_consultation_flow_regression.py`

## Fake Test Data

- Maya Author / maya@example.com / +1 555 987 6543
- John Smith / john@example.com / 5551234567
