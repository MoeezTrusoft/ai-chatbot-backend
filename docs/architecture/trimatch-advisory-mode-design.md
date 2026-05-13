# Tri-Match Extra Advisory Mode Design

## Status

Design only.

This document does not implement advisory mode.

Current allowed runtime modes remain:

```text
TRIMATCH_EXTRA_MODE=off
TRIMATCH_EXTRA_MODE=shadow
Future proposed mode:

TRIMATCH_EXTRA_MODE=advisory

Advisory mode must not be implemented until the readiness gates in this document pass.

Purpose

Tri-Match extra advisory mode is a future intermediate promotion stage between runtime shadow review and any tiebreaker or shortcut behavior.

Its purpose is to allow extra, human-approved, calibrated RulePacks to produce a recommendation that can be logged, reviewed, and shown to internal diagnostics without changing the final customer-facing runtime path.

Current reinforcement state

The current reinforcement loop supports:

staged Rules Army v2
→ shadow evaluation
→ human-review candidate schemas
→ candidate miner
→ approved candidate compiler
→ runtime shadow loader
→ disagreement logging
→ calibration report
→ shadow runtime review
→ runtime review candidate miner
→ human review queue
→ safe batch template
→ review template ingestion
→ ingestion audit report
→ governance smoke tests

The next step is not activation.

The next step is a design-governed advisory mode that remains non-routing and non-overriding.

Non-negotiable safety rules

Every advisory-mode implementation must preserve these rules:

Do not invent pricing.
Do not expose unapproved pricing values.
Do not bypass the deterministic quote engine.
Do not create agreement fee values unless an approved deterministic quote exists.
Do not generate NDA or agreement text outside approved templates.
Do not hallucinate portfolio or sample links.
Do not allow negated services to become requested services.
Do not treat counterfactual pressure as approval.
Do not let extra RulePacks affect final response generation.
Human review is required before generated candidates become staged rules.
Advisory output must never override active Tri-Match, ensemble, hardening, tools, or response generation.
Advisory output must never create pricing, NDA, agreement, portfolio, or routing side effects.
Definition of advisory mode

Advisory mode means:

extra RulePack can produce an internal recommendation
but cannot alter final intent, state, tools, pricing, documents, portfolio, RAG, or response text

Advisory mode is stronger than shadow only in observability.

It may add an internal event such as:

trimatch.extra_advisory_recommended

It must not add any event that implies final routing changed.

Explicit non-goals

Advisory mode is not:

a final classifier
a tiebreaker
a shortcut path
a pricing gate
a document gate
a portfolio URL source
a response-generation input
an extraction/state-update input
a replacement for intent/hardening.py
a production activation of Rules Army v2
Required runtime behavior

When TRIMATCH_EXTRA_MODE=advisory is eventually implemented, runtime flow must remain:

active Tri-Match
→ ensemble classifier
→ deterministic hardening
→ extraction/state updates
→ pricing/portfolio/document tools if allowed
→ response generator

The extra advisory RulePack may run beside this flow:

extra advisory Tri-Match
→ advisory recommendation event
→ diagnostics/reporting only

It must not feed into:

EnsembleIntentClassifier.classify(...)
harden_intent_from_message(...)
CombinedExtractor
StateApplier
pricing quote requests
portfolio requests
document generation
RAG allow/skip logic
SonnetResponseGenerator.generate(...)
final ChatTurnResponse.intent
Proposed event

Advisory mode should log a structured event:

trimatch.extra_advisory_recommended

Suggested payload:

{
  "extra_advisory": {
    "query_primary": "service_question",
    "service_primary": "editing_proofreading",
    "funnel_stage": "service_discovery",
    "confidence": 0.91,
    "evidence_count": 2,
    "shortcut_eligible": false
  },
  "final": {
    "query_primary": "service_question",
    "service_primary": "editing_proofreading",
    "funnel_stage": "service_discovery",
    "confidence": 0.82
  },
  "recommendation": {
    "dimension": "service_primary",
    "recommended_value": "editing_proofreading",
    "matches_final": true,
    "reason": "extra approved RulePack agreed with final service intent"
  },
  "advisory_applied": false,
  "side_effects_allowed": false
}

Required constants:

{
  "advisory_applied": false,
  "side_effects_allowed": false
}

These fields must remain false in advisory mode.

Settings design

Current setting:

trimatch_extra_mode: Literal["off", "shadow"] = "off"

Future implementation may extend it to:

trimatch_extra_mode: Literal["off", "shadow", "advisory"] = "off"

This must be guarded by tests proving:

off creates no extra engine
shadow logs shadow votes only
advisory logs advisory recommendations only
neither shadow nor advisory changes final intent
neither mode passes extra RulePack results into response generation
Eligible RulePacks

Advisory mode may only load RulePacks from human-reviewed staged output, for example:

data/trimatch/reinforcement/staged_from_reviews/

Eligible rules must be:

human-reviewed
compiled from approve or edit_and_approve
shortcut-disabled
shadow-reviewed
calibration-reviewed
covered by negative examples
safe against negation, hedging, and counterfactual cases

Rules Army v2 staged assets must not be globally activated through advisory mode.

Minimum evidence gates

Before advisory implementation:

shadow runtime review: valid=true
shadow runtime failed_turns=0
production-flow safety_failures=0
Rules Army v2 shadow regressions=0
reinforcement validation valid=true
review ingestion audit valid=true
governance smoke tests passing

Suggested minimum before enabling advisory in any non-local environment:

at least 20 manually approved candidates
at least 3 positive examples per candidate
at least 3 negative examples per candidate
zero known negation regressions
zero known pricing/document/portfolio safety regressions
two consecutive passing shadow runtime reviews
one passing production-flow regression after staged RulePack update
Forbidden advisory targets

Advisory recommendations must not be used for:

pricing number display
timeline number display
discounts
payment plans
NDA generation
agreement generation
agreement fee fields
portfolio/sample links
customer-facing claims
legal language
final response routing
shortcut classification

Even if advisory detects pricing_question, nda_request, agreement_request, or portfolio_request, it may only log diagnostics.

Implementation outline for a future branch

A future implementation branch may add:

feat/trimatch-extra-advisory-mode

Possible changes:

Extend Settings.trimatch_extra_mode.
Add build_trimatch_advisory_engine(...) or generalize the extra engine builder.
Keep extra result separate from active Tri-Match.
Add trimatch.extra_advisory_recommended event.
Add tests proving final intent and final response are unchanged.
Add calibration/reporting support for advisory recommendations.
Add runbook documenting local-only use first.
Required tests for future implementation

Future implementation must include tests for:

Disabled mode
TRIMATCH_EXTRA_MODE=off
→ no extra advisory/shadow event
Shadow mode compatibility
TRIMATCH_EXTRA_MODE=shadow
→ existing shadow behavior unchanged
Advisory recommendation only
TRIMATCH_EXTRA_MODE=advisory
→ advisory event logged
→ final intent unchanged
→ response unchanged
Pricing safety
advisory recommends pricing_question
→ no unapproved price shown
→ deterministic quote engine still owns numbers
NDA/agreement safety
advisory recommends nda_request/agreement_request
→ no template bypass
→ no agreement fee without approved quote
Portfolio safety
advisory recommends portfolio_request
→ no unapproved URLs
→ portfolio registry remains source of truth
Negation safety
I do not need ghostwriting, I need editing
→ advisory must not make ghostwriting requested
Counterfactual safety
If I wanted ghostwriting, what would happen?
→ advisory must not treat ghostwriting as approved scope
Rollback plan

Advisory mode must be controlled by:

TRIMATCH_EXTRA_MODE=off

Rollback must require only configuration change, not code rollback.

If advisory event volume, disagreement rate, or safety reports become suspicious:

TRIMATCH_EXTRA_MODE=off
Decision

Do not implement advisory mode yet.

First complete:

design review
→ governance smoke test stable on main
→ manual review batch ingestion with real human-reviewed rows
→ shadow runtime review with real approved candidate RulePack
→ audit report valid
→ calibration report valid

Only then create an implementation PR for advisory mode.
