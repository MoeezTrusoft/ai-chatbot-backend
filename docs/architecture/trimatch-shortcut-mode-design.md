# Tri-Match Shortcut Mode Design

## Status

Design only.

This document does not implement shortcut mode.

Current safe promotion state:

```text
TRIMATCH_EXTRA_MODE=off
TRIMATCH_EXTRA_MODE=shadow
TRIMATCH_EXTRA_MODE=advisory
TRIMATCH_EXTRA_MODE=tiebreaker_candidate

Future proposed mode:

TRIMATCH_EXTRA_MODE=shortcut_candidate

Shortcut mode must not be implemented until all readiness gates in this document pass.

Purpose

Shortcut mode is the highest-risk Tri-Match promotion stage.

Unlike shadow, advisory, and tiebreaker candidate modes, shortcut mode may eventually allow an approved deterministic rule to bypass part of the usual intent-classification path for very narrow low-risk cases.

Because of that, shortcut mode must be treated as a controlled optimization, not a general classifier.

Non-goals

Shortcut mode is not:

a replacement for the ensemble
a replacement for hardening
a replacement for the deterministic quote engine
a replacement for portfolio registry lookup
a replacement for NDA/agreement templates
a way to skip extraction/state safety
a way to skip response generation safety
a way to activate all Rules Army v2 rules globally
Allowed shortcut scope

Initial shortcut mode may only be considered for:

exact rules
regex rules
human-approved rules
non-sensitive service/query clarification cases

Shortcut mode must not use:

semantic-only matching
fuzzy matching
unreviewed generated candidates
shadow-only candidates
advisory-only candidates
rules with shortcut_allowed=false
rules without negative examples
rules without negation/counterfactual tests
Required rule requirements

A shortcut-eligible rule must be:

human-approved
compiled from approve or edit_and_approve review rows
explicitly marked shortcut_allowed=true
exact or regex only
covered by at least 3 positive examples
covered by at least 3 negative examples
covered by negation tests
covered by counterfactual tests
covered by pricing/document/portfolio safety tests
calibrated
audited
rollback-safe
Forbidden shortcut query intents

Shortcut mode must never directly produce these final query intents:

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

These may be classified only through the normal guarded pipeline.

Forbidden shortcut effects

Shortcut mode must never:

show pricing
show timeline values
offer discounts
create payment plans
generate NDA text
generate agreement text
populate agreement fee fields
return portfolio/sample URLs
call tools directly
call pricing_engine.quote directly
call portfolio_engine.request_samples directly
call document templates directly
call rag_retriever.retrieve directly
call response_generator.generate directly

Shortcut mode may only create an internal intent recommendation.

Side-effect rule

Even if a shortcut is used:

{
  "shortcut_applied": true,
  "side_effects_allowed": false
}

side_effects_allowed must always remain false.

Required event

Future implementation should log:

trimatch.extra_shortcut_considered

Suggested blocked payload:

{
  "shortcut": {
    "eligible": false,
    "applied": false,
    "dimension": null,
    "recommended_value": null,
    "rule_id": null,
    "reason": "blocked: not shortcut eligible",
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

Suggested applied payload:

{
  "shortcut": {
    "eligible": true,
    "applied": true,
    "dimension": "service_primary",
    "recommended_value": "editing_proofreading",
    "rule_id": "approved_rule_001",
    "reason": "applied: exact human-approved shortcut rule matched safely",
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
Allowed dimensions

Initial shortcut mode may only affect:

service_primary
query_primary

It must not affect:

funnel_stage
pricing fields
timeline fields
document fields
portfolio fields
customer state fields
RAG routing fields
response text
Required system gates

Before any shortcut implementation PR:

tiebreaker audit valid=true
tiebreaker application governance smoke passes
advisory audit valid=true
shadow runtime review valid=true
review ingestion audit valid=true
reinforcement validation valid=true
calibration report valid=true
production_flow_safety_failures=0
shadow_regressions=0
shortcut design accepted
Required tests before implementation

Future shortcut implementation must include tests for:

off mode creates no shortcut events
shadow mode unchanged
advisory mode unchanged
tiebreaker_candidate mode unchanged
shortcut_candidate logs shortcut considered event
shortcut exact service rule can apply only when safe
shortcut regex service rule can apply only when safe
shortcut cannot apply pricing_question
shortcut cannot apply timeline_question
shortcut cannot apply portfolio_request
shortcut cannot apply nda_request
shortcut cannot apply agreement_request
shortcut cannot apply payment_question
shortcut cannot apply ready_to_buy
shortcut cannot apply spam/off_topic
shortcut cannot apply negated services
shortcut cannot apply counterfactual services
shortcut cannot apply if shortcut_allowed=false
shortcut cannot apply semantic-only rules
shortcut cannot apply fuzzy-only rules
shortcut cannot apply unreviewed candidates
side_effects_allowed remains false
normal response generator still owns response text
pricing engine still owns pricing
portfolio registry still owns portfolio URLs
document templates still own NDA/agreement text
Required audit report

Future shortcut audit report must include:

shortcut_event_count
eligible_count
applied_count
blocked_reason_counts
applied_dimension_counts
applied_value_counts
pricing_sensitive_block_count
document_sensitive_block_count
portfolio_sensitive_block_count
negated_block_count
counterfactual_block_count
side_effects_allowed_count

The audit must fail if:

side_effects_allowed_count > 0
pricing-sensitive applied_count > 0
document-sensitive applied_count > 0
portfolio-sensitive applied_count > 0
negated applied_count > 0
counterfactual applied_count > 0
unreviewed rule applied_count > 0
shortcut_allowed=false applied_count > 0
semantic-only applied_count > 0
fuzzy-only applied_count > 0
Rollback

Shortcut mode must be disabled through config:

TRIMATCH_EXTRA_MODE=off

Rollback must not require:

database migration
code rollback
data deletion
manual event cleanup
Decision

Do not implement shortcut mode yet.

Next safe step after this design is:

test/trimatch-shortcut-governance-smoke

Only after that should a future implementation branch be considered:

feat/trimatch-shortcut-candidate-considered

