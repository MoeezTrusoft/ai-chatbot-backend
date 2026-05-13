# Tri-Match Tiebreaker Application Gates

## Status

Design only.

This document defines the required gates before any future branch may set:

```json
{
  "decision": {
    "applied": true
  }
}
Current implementation status:

TRIMATCH_EXTRA_MODE=tiebreaker_candidate
→ computes eligibility
→ logs trimatch.extra_tiebreaker_considered
→ keeps decision.applied=false
→ keeps safety.side_effects_allowed=false
Purpose

Tiebreaker application is the first Tri-Match promotion step that may influence final intent.

Because of that, it must be treated as a high-risk controlled feature.

The goal is not to replace the ensemble, hardening, extraction, pricing, portfolio, documents, RAG, or response generation.

The goal is only to allow a human-approved extra RulePack to resolve a narrow safe intent disagreement.

Non-negotiable rule

Even when a tiebreaker is applied:

response generation must still go through the normal response generator
pricing must still go through the deterministic quote engine
portfolio links must still come from the portfolio registry
NDA/agreement text must still come from approved templates
RAG allow/skip logic must remain governed by final intent safety rules
Allowed application dimensions

Initial applied tiebreakers may only affect:

service_primary
query_primary

Do not apply tiebreakers to:

funnel_stage
pricing fields
timeline fields
document fields
portfolio fields
customer state fields
RAG routing fields
response text
Forbidden query intents

A tiebreaker must never apply these query intent values:

pricing_question
timeline_question
portfolio_request
nda_request
agreement_request
payment_question
complaint_or_objection
ready_to_buy
spam_or_abuse
off_topic

These may remain observable in advisory/audit reports only.

Required safety gates

A tiebreaker may apply only if all are true:

decision.eligible=true
decision.applied=false before application
safety.side_effects_allowed=false
safety.pricing_sensitive=false
safety.document_sensitive=false
safety.portfolio_sensitive=false
safety.negated=false
safety.counterfactual=false
dimension is service_primary or query_primary
recommended_value is not forbidden
extra recommendation differs from final intent
final confidence is below configured threshold
extra recommendation confidence is above configured threshold
extra evidence_count >= configured minimum
Required system gates

Before an implementation PR may set applied=true, all must pass:

tiebreaker audit valid=true
tiebreaker applied_count=0 before implementation
tiebreaker side_effects_allowed_count=0
advisory audit valid=true
shadow runtime review valid=true
review ingestion audit valid=true
reinforcement validation valid=true
calibration report valid=true
production_flow_safety_failures=0
shadow_regressions=0
governance smoke tests pass
Manual review gates

Tiebreaker application may only use rules that are:

human-approved
compiled from approve or edit_and_approve review rows
shortcut_allowed=false
covered by positive examples
covered by negative examples
covered by negation tests
covered by counterfactual tests
covered by pricing/document/portfolio safety tests
Required event shape

Future implementation must continue to log:

trimatch.extra_tiebreaker_considered

When application remains blocked:

{
  "decision": {
    "eligible": false,
    "applied": false,
    "dimension": null,
    "recommended_value": null,
    "blocked_reasons": []
  },
  "safety": {
    "side_effects_allowed": false
  }
}

When application is allowed:

{
  "decision": {
    "eligible": true,
    "applied": true,
    "dimension": "service_primary",
    "recommended_value": "editing_proofreading",
    "reason": "applied: safe tiebreaker resolved eligible intent disagreement",
    "blocked_reasons": []
  },
  "safety": {
    "pricing_sensitive": false,
    "document_sensitive": false,
    "portfolio_sensitive": false,
    "negated": false,
    "counterfactual": false,
    "side_effects_allowed": false
  }
}

Important: even when applied=true, side_effects_allowed remains false.

Response pipeline rule

The tiebreaker may modify only the internal final IntentVote before downstream safe systems run.

It must not directly call or bypass:

pricing_engine.quote
portfolio_engine.request_samples
document templates
rag_retriever.retrieve
response_generator.generate
tool_dispatcher.invoke

The existing pipeline remains responsible for downstream behavior.

Required tests before implementation

Future implementation branch must include tests for:

safe service_primary tiebreaker can apply
safe query_primary tiebreaker can apply
pricing_question tiebreaker cannot apply
timeline_question tiebreaker cannot apply
portfolio_request tiebreaker cannot apply
nda_request tiebreaker cannot apply
agreement_request tiebreaker cannot apply
negated service cannot apply
counterfactual service cannot apply
high final confidence blocks application
matching final recommendation blocks application
unsupported dimension blocks application
side_effects_allowed remains false even when applied
response generation still uses normal generator
pricing still requires deterministic quote engine
portfolio still requires registry
documents still require templates
Required audit updates before implementation

The tiebreaker audit report must be updated to track:

eligible_count
applied_count
blocked_reason_counts
applied_dimension_counts
applied_value_counts
sensitive_block_count
final_intent_changed_count
side_effects_allowed_count

The audit must fail if:

side_effects_allowed_count > 0
pricing-sensitive applied_count > 0
document-sensitive applied_count > 0
portfolio-sensitive applied_count > 0
negated applied_count > 0
counterfactual applied_count > 0
Rollback

Tiebreaker application must be disabled by configuration only:

TRIMATCH_EXTRA_MODE=off

No database migration, code rollback, or data deletion should be required.

Decision

Do not implement applied=true until this gates document and readiness runbook are merged.

The next implementation branch should be limited to:

feat/trimatch-tiebreaker-application-gated

That branch must keep the implementation narrow, test-heavy, and fully reversible.
