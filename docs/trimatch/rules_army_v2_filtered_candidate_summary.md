# Rules Army v2 Filtered Candidate Summary

Status: `shadow_candidate_only`
Active promotion allowed: `False`

## Reason

Filtered v2 candidate fixed known collision probes, but full verify_trimatch_rules.py gate still fails precision/recall floors.

## Counts

- Total rules: `947`
- Enabled rules: `933`
- Disabled rules: `14`
- Shortcut allowed: `393`
- Dimensions: `{'funnel_stage': 139, 'query_intent': 336, 'service_intent': 458}`
- Layers: `{'exact': 621, 'regex': 101, 'pattern': 137, 'semantic': 74}`

## Required Before Active Promotion

- Raise funnel_stage:exact precision to >= 0.97
- Raise funnel_stage:pattern recall to >= 0.45 or update verifier design with a justified v2 gate
- Raise query_intent:pattern recall to >= 0.45 or update verifier design with a justified v2 gate
- Raise service_intent:pattern recall to >= 0.45 or update verifier design with a justified v2 gate
- Add context-sidecar arbitration for high-specificity rules such as book trailer suppressing broad create-a-book rules
- Run advanced negation/multi-service/counterfactual eval set

## Safe Behavior Probe

- `can_you_help_ebook_only`: service_question, not greeting
- `i_need_help_memoir`: no false greeting
- `book_trailer`: video_trailer
- `simple_terms`: no agreement_request
- `legal_terms_for_agreements`: service_question only

