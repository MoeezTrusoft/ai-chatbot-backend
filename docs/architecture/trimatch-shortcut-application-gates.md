# Tri-Match Shortcut Application Gates

## Status

Design only.

This document defines the required gates before any future branch may set:

```json
{
  "shortcut": {
    "applied": true
  }
}
Current safe state:

TRIMATCH_EXTRA_MODE=shortcut_candidate
→ logs trimatch.extra_shortcut_considered
→ computes shortcut eligibility
→ keeps shortcut.applied=false
→ keeps safety.side_effects_allowed=false
→ does not change final intent
Purpose

Shortcut application is the highest-risk Tri-Match promotion stage.

It may eventually allow a narrow, human-approved, exact/regex rule to influence final intent before the normal downstream pipeline continues.

It must not become a general classifier shortcut, pricing shortcut, document shortcut, portfolio shortcut, RAG shortcut, or response-generation shortcut.

Non-negotiable safety rule

Even when a shortcut applies:

{
  "shortcut": {
    "applied": true
  },
  "safety": {
    "side_effects_allowed": false
  }
}

side_effects_allowed must remain false.

Shortcut application may update only the internal intent recommendation.

It must not directly call:

pricing_engine.quote
portfolio_engine.request_samples
document templates
rag_retriever.retrieve
response_generator.generate
tool_dispatcher.invoke
Allowed dimensions

Shortcut application may only affect:

service_primary
query_primary

Shortcut application must not affect:

funnel_stage
pricing fields
timeline fields
document fields
portfolio fields
customer state fields
RAG routing fields
response text
tool calls
Required shortcut rule gates

A shortcut may apply only if all are true:

shortcut.eligible=true
shortcut.applied=false before application
dimension is service_primary or query_primary
recommended_value is not forbidden
rule_id is present
rule layer is exact or regex
rule shortcut_allowed=true
rule is human-approved
rule has positive examples
rule has negative examples
rule has negation coverage
rule has counterfactual coverage
rule has pricing/document/portfolio safety coverage
Forbidden query intents

Shortcut application must never apply these query intents:

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

These must remain classified through the normal guarded pipeline.

Required safety gates

Shortcut application may apply only if all are true:

safety.pricing_sensitive=false
safety.document_sensitive=false
safety.portfolio_sensitive=false
safety.negated=false
safety.counterfactual=false
safety.side_effects_allowed=false
Required evidence gates

Shortcut application must block if any are true:

semantic evidence is present
fuzzy evidence is present
shortcut_eligible=false
shortcut_eligible missing
negated evidence is present
counterfactual evidence is present
recommended value already matches final intent
unsupported dimension is present
rule_id is missing
Required audit gates

Before implementation may set shortcut.applied=true, all must pass:

shortcut audit valid=true
shortcut side_effects_allowed_count=0
shortcut applied_count=0 before implementation
shortcut sensitive_block_count >= 3
shortcut pricing_sensitive_count >= 1
shortcut document_sensitive_count >= 1
shortcut portfolio_sensitive_count >= 1
tiebreaker audit valid=true
advisory audit valid=true
shadow runtime review valid=true
review ingestion audit valid=true
reinforcement validation valid=true
calibration report valid=true
production_flow_safety_failures=0
shadow_regressions=0
mypy passes
Required future audit fields

The shortcut audit report must continue to track:

eligible_count
eligible_not_applied_count
applied_count
side_effects_allowed_count
sensitive_block_count
applied_dimension_counts
applied_value_counts
applied_rule_id_counts
blocked_reason_counts
pricing_sensitive_count
document_sensitive_count
portfolio_sensitive_count

After application exists, the audit must fail if any are true:

side_effects_allowed_count > 0
pricing-sensitive shortcut applied
document-sensitive shortcut applied
portfolio-sensitive shortcut applied
negated shortcut applied
counterfactual shortcut applied
semantic/fuzzy shortcut applied
shortcut_allowed=false shortcut applied
unreviewed rule applied
unsupported dimension applied
missing rule_id applied
Required implementation behavior

Future application branch may do this only:

if shortcut is eligible and all gates pass:
  set shortcut.applied=true
  update internal IntentVote query_primary or service_primary
  append evidence note
  continue normal downstream pipeline

It must not:

return early
skip extraction
skip pricing engine
skip portfolio engine
skip document safeguards
skip RAG safety
skip response generator
invoke tools directly
Required tests before application

Future implementation must include tests for:

safe exact service shortcut can apply
safe regex service shortcut can apply
safe exact query shortcut can apply
safe regex query shortcut can apply
pricing_question cannot apply
timeline_question cannot apply
portfolio_request cannot apply
nda_request cannot apply
agreement_request cannot apply
payment_question cannot apply
ready_to_buy cannot apply
spam_or_abuse cannot apply
off_topic cannot apply
semantic evidence cannot apply
fuzzy evidence cannot apply
shortcut_allowed=false cannot apply
negated evidence cannot apply
counterfactual evidence cannot apply
missing rule_id cannot apply
unsupported dimension cannot apply
side_effects_allowed remains false when applied
normal response generator still owns response text
pricing engine still owns pricing
portfolio registry still owns portfolio URLs
document templates still own NDA/agreement text
Rollback

Shortcut application must be disabled by config:

TRIMATCH_EXTRA_MODE=off

Rollback must not require:

database migration
code rollback
event cleanup
data deletion
manual rule deletion
Decision

Do not implement shortcut application until this document and the readiness runbook are merged.

Next implementation branch should be:

feat/trimatch-shortcut-application-gated

