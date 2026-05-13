# Tri-Match Extra Tiebreaker Candidate Mode Design

## Status

Design only.

This document does not implement tiebreaker mode.

Current safe promotion state:

```text
TRIMATCH_EXTRA_MODE=off
TRIMATCH_EXTRA_MODE=shadow
TRIMATCH_EXTRA_MODE=advisoryFuture proposed mode:

TRIMATCH_EXTRA_MODE=tiebreaker_candidate

Tiebreaker mode must not be implemented until all readiness gates pass.

Purpose

Tiebreaker candidate mode is a future controlled promotion stage where a human-approved, calibrated extra RulePack may influence final intent only under strict conflict conditions.

It is higher risk than advisory mode.

Advisory mode only logs recommendations.

Tiebreaker candidate mode may eventually help resolve intent disagreement, but only when the final decision is otherwise uncertain and the case is not safety-sensitive.

Current completed foundation

The current system already supports:

shadow runtime review
→ runtime review candidate miner
→ human review queue
→ safe review batch template
→ review template ingestion
→ review ingestion audit report
→ reinforcement governance smoke test
→ advisory mode design
→ advisory mode implementation
→ advisory audit report

This design builds on that foundation.

Non-negotiable safety rules

Tiebreaker mode must never affect:

pricing values
timeline values
discounts
payment plans
NDA generation
agreement generation
agreement fee fields
portfolio/sample URLs
legal language
final response text directly
shortcut classification
extraction/state updates unless final intent governance approves it

The deterministic quote engine remains the only owner of pricing and timeline numbers.

Document templates remain the only source for NDA/agreement text.

Portfolio registry remains the only source for portfolio/sample links.

Definition

Tiebreaker candidate mode means:

extra human-approved RulePack may suggest a final intent only when:
1. active Tri-Match, ensemble, and hardening disagree
2. the case is not safety-sensitive
3. confidence and calibration gates pass
4. the recommended dimension is allowed
5. the recommendation is logged with full audit payload

It is not a shortcut.

It is not a replacement for the ensemble.

It is not allowed to bypass human-reviewed governance.

Allowed dimensions

Tiebreaker mode may initially support only:

service_primary
query_primary

Funnel stage must remain advisory-only until separately evaluated.

Forbidden query intents

Tiebreaker mode must not decide or override these query intents:

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

These may be logged as advisory recommendations only.

Forbidden services

No service should be forbidden globally, but service recommendations must be blocked when:

user negates the service
user speaks counterfactually
user asks only hypothetically
user explicitly excludes the service
service recommendation would trigger pricing/document/portfolio behavior
Required event

Future implementation should log:

trimatch.extra_tiebreaker_considered

Suggested payload:

{
  "extra_tiebreaker": {
    "query_primary": "service_question",
    "service_primary": "editing_proofreading",
    "funnel_stage": null,
    "confidence": 0.93,
    "evidence_count": 2
  },
  "before": {
    "active_trimatch": {},
    "ensemble": {},
    "final_before_tiebreaker": {}
  },
  "decision": {
    "eligible": false,
    "applied": false,
    "dimension": null,
    "recommended_value": null,
    "reason": "blocked: no qualifying disagreement"
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

Required default:

{
  "decision": {
    "applied": false
  },
  "safety": {
    "side_effects_allowed": false
  }
}
Eligibility gates

A tiebreaker may be considered only if all are true:

extra RulePack is human-approved
compiled rule has shortcut_allowed=false
shadow runtime review passed
advisory audit passed
governance smoke passed
review ingestion audit valid
calibration report valid
production-flow safety failures = 0
shadow regressions = 0

Runtime gates:

active final confidence below threshold
provider disagreement exists
extra recommendation confidence above threshold
extra recommendation has evidence_count >= 1
message is not pricing/document/portfolio sensitive
message is not negated/counterfactual
recommended query/service is allowed
Suggested thresholds

Initial local-only values:

final_confidence_max_for_tiebreaker = 0.72
extra_confidence_min_for_tiebreaker = 0.90
min_evidence_count = 1

These values must remain config-controlled.

Required tests before implementation

Future implementation must add tests for:

Off mode
TRIMATCH_EXTRA_MODE=off
→ no tiebreaker events
Shadow mode unchanged
TRIMATCH_EXTRA_MODE=shadow
→ existing shadow events only
Advisory mode unchanged
TRIMATCH_EXTRA_MODE=advisory
→ advisory recommendation only
→ no tiebreaker event
Tiebreaker considered but blocked
safe service message
→ event logged
→ applied=false when no qualifying disagreement
Tiebreaker applied for safe service conflict
provider/active disagreement
extra approved service recommendation
safe service_question
→ applied=true only for allowed dimension
Pricing safety
extra recommends pricing_question
→ applied=false
→ no pricing shown
NDA/agreement safety
extra recommends nda_request/agreement_request
→ applied=false
→ no template bypass
Portfolio safety
extra recommends portfolio_request
→ applied=false
→ no unapproved URLs
Negation safety
I do not need ghostwriting, I need editing
→ ghostwriting cannot be applied
Counterfactual safety
If I wanted ghostwriting...
→ ghostwriting cannot be applied
Rollback

Tiebreaker mode must be disabled with:

TRIMATCH_EXTRA_MODE=off

Rollback must not require database migration or code rollback.

Decision

Do not implement tiebreaker candidate mode yet.

Next required steps:

design review
→ tiebreaker governance tests
→ local-only tiebreaker implementation
→ audit report
→ manual review
→ shadow/advisory comparison

