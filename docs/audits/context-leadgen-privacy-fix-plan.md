# Context, Lead-Gen & Privacy Fix Plan

## Files Inspected

- `src/bookcraft/components/leads/contact.py` — ContactCaptureDetector, merge_with_state
- `src/bookcraft/components/leads/objective.py` — LeadObjectiveEngine
- `src/bookcraft/components/actions/slot_resolver.py` — has_email_or_phone()
- `src/bookcraft/components/context/pack_builder.py` — contact_capture_status logic
- `src/bookcraft/components/context/enforcement.py` — _contact_ready() helper
- `src/bookcraft/components/sales/consultation_objective.py` — ConsultationObjectiveEngine
- `src/bookcraft/components/response/generator.py` — _cta_for_intent, _response_user_prompt
- `src/bookcraft/components/response/quality_gate.py` — all quality checks
- `src/bookcraft/components/response/planner.py` — ResponsePlanner
- `src/bookcraft/components/storage/state_sanitizer.py` — redaction sentinels
- `src/bookcraft/infra/redaction.py` — REDACTED_EMAIL, REDACTED_PHONE, REDACTED_NUMBER
- `tests/evals/conversations/consultation_booking.yml` — existing eval

## Root Causes Found

### 1. Redacted Placeholders Treated as Real Contact Values (CRITICAL)
The state sanitizer replaces real PII with sentinels like `[REDACTED_EMAIL]`, `[REDACTED_PHONE]`,
`[REDACTED_NAME]` before DB persistence. When state is reloaded on the next turn, those sentinel
strings are non-empty, so `bool(contact_info.get("email"))` returns True — making the system
believe valid contact exists when it does not.

Affected locations:
- `pack_builder.py` lines 249–251: raw bool() checks on contact_info dict
- `enforcement.py` lines 488–490: same pattern
- `slot_resolver.py` line 317: `has_email_or_phone()` uses simple bool check
- `contact.py` merge_with_state lines 164–167: doesn't filter sentinels

### 2. No Bare-Block Contact Extraction
The contact detector only uses phrases like "my name is X". A message like:
"John Smith john@example.com 5551234567" fails name extraction, leaving the
lead partially captured and re-prompting for name on next turn.

### 3. Consultation Intent Does Not Consistently Override Discovery
After contact is captured, when user says "Just schedule my consultation for tomorrow",
the planner/generator may still fall through to generic discovery questions
(genre, page count, format) instead of asking only for preferred time window.

### 4. No "Already Shared" Recovery Detector
No existing logic handles: "I just shared it to you above", "I already gave you my info".
When triggered on a contact_ready state, the bot should acknowledge and move forward.

### 5. PII Echo in Responses — No Quality Gate Check
No check prevents the generator from repeating raw user email/phone in assistant text.
The system can output "Our team will contact you at [email]" treating user PII as
company contact details — the exact complaint in the customer incident.

### 6. No Complaint/Privacy Trust-Recovery Mode
When user says "what the fuck that's my contact details you're sharing", the bot should:
- Immediately acknowledge the mistake
- Stop all sales/discovery questions
- Not repeat the PII
Currently no such mode exists.

## Changes to Be Made

### Phase 2: contact_utils.py — Redacted Placeholder Guard
Create `src/bookcraft/components/leads/contact_utils.py` with:
- `REDACTED_SENTINELS` set
- `is_real_contact_value(value)` — returns False for None, empty, or sentinel strings
- `has_real_name(contact_info)`, `has_real_email(contact_info)`, `has_real_phone(contact_info)`
- `contact_is_ready(contact_info)` — name + (email OR phone), all real values

Update:
- `pack_builder.py`: replace bool(contact_info.get(...)) with has_real_*()
- `enforcement.py _contact_ready()`: use contact_is_ready()
- `slot_resolver.py has_email_or_phone()`: filter sentinels
- `contact.py merge_with_state()`: filter sentinel values before merging

### Phase 3: Bare Contact Block Extraction
Add `_extract_bare_contact_name()` to `contact.py` and call it as fallback when
structured name patterns fail but email/phone was found.

### Phase 4: Consultation Priority Override
Add `user_requests_schedule_now()` detector and `_already_shared_recovery()` in
`safety/input_guard.py` or a new `leads/contact_recovery.py` module.

In `consultation_objective.py`:
- Detect "already_shared" signal and map to recovery path
- When contact_ready + consultation intent + time missing → ONLY ask for time window

In `response/generator.py`:
- Add `_cta_consultation_time_only()` template
- When `consultation_time_capture` primary goal, use time-only CTA

### Phase 5: Already-Shared Recovery
Add `user_claims_already_shared(text)` helper.
Wire into pre-processing: if triggered + contact state partial/ready → emit
recovery response acknowledging contact, not re-asking.

### Phase 6: PII Echo Quality Gate
In `quality_gate.py`, add Check 21: PII echo suppression.
Scan assistant response text for user's email/phone from thread state.
Fail if found unless it's an explicit masked confirmation context.

### Phase 7: Response Obedience (already partially done; reinforce)
Ensure `_cta_for_intent()` returns `preferred_call_time` CTA when
`response_plan.primary_goal == "consultation_time_capture"`.

### Phase 8: Complaint/Privacy Recovery Mode
Extend `safety/input_guard.py` with complaint/privacy patterns.
Return `action="warn"` + `system_message` hint for complaint mode.
Add `privacy_complaint` detection that prevents PII echo in response.

### Phase 9: Tests/Evals
- `tests/unit/test_contact_utils.py`
- `tests/evals/conversations/context_contact_consultation_recovery.yml`
- Key assertions per spec.

## Tests to Add (unit)
1. test_redacted_placeholders_do_not_count_as_contact_ready
2. test_bare_contact_block_extracts_name_email_phone
3. test_consultation_after_contact_does_not_ask_contact_again
4. test_user_says_already_shared_triggers_recovery
5. test_response_never_relabels_user_pii_as_company_contact
6. test_free_consultation_overrides_discovery_questions
7. test_complaint_recovery_does_not_continue_sales_script
8. test_known_genre_not_reasked_after_rough_notes
9. test_known_contact_not_reasked_after_contact_ready
10. test_response_plan_max_one_question_enforced

## Risks and Rollback Notes
- Changing `has_email_or_phone()` in slot_resolver.py could affect NDA/agreement
  scheduling if existing tests rely on sentinel values being "truthy".
  → Mitigation: keep existing function signature, change internal logic only.
  → Run full test suite after each phase.
- Bare name extraction must be conservative (1-5 words only, fake-name filter).
  → Risk of false positives in long messages.
  → Mitigation: only activate when email OR phone found in same message.
- PII echo gate is new; may cause quality gate failures on legitimate responses
  that include contact in a professional context (e.g., "your booking at ...").
  → Allow masked variants: "contact details on file", "email ending in ...".

## PII Policy Notes
- Never include real customer PII in tests. Use john@example.com, 5551234567, John Smith.
- Do not log raw PII; use boolean flags or masked values in observability fields.
