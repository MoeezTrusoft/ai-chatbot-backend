# Tri-Match Human Review Runbook

## Purpose

Tri-Match reinforcement must be human-approved. LLMs, NLP tools, diagnostics, and shadow evaluations may propose candidate rules, but they must not directly modify production classification behavior.

The workflow is:

```text
production signal
→ candidate rule proposal
→ human review
→ staged rule
→ eval
→ shadow
→ advisory/tiebreaker promotion
→ eventual shortcut eligibility
Review sources

Candidate rules may come from:

diagnostic failures
live LLM disagreement
Tri-Match vs LLM disagreement
low-confidence classifications
user corrections
consultant corrections
production-flow failures
manual authoring
shadow evaluation findings
Required evidence before approval

Each candidate must include:

candidate type
target layer
target dimension
target label
source id
proposal value
at least 3 positive examples
at least 3 negative examples
risk note
suggested weight
approval status
Reviewer decisions

Allowed review decisions:

approve
reject
edit_and_approve
needs_more_examples
duplicate
unsafe
defer
Promotion scopes

Allowed promotion scopes:

none
staged_only
shadow
advisory
tiebreaker_candidate
shortcut_candidate

Shortcut promotion requires a separate safety gate and is not allowed from first review alone.

Safety rules

Never approve a rule that:

invents pricing
weakens NDA/agreement gating
allows fake portfolio links
ignores negation
treats counterfactual pressure as approval
overmatches broad generic language
lacks negative examples
Recommended review cadence

Weekly:

review new candidates
reject unsafe/broad rules
approve precise rules into staged-only status
add false positives to eval corpus

Monthly:

compare Tri-Match against individual LLM providers
review disagreement logs
promote only calibrated high-precision rules
