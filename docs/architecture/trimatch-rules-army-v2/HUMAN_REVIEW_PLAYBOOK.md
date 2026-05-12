# Human Review Playbook

Reviewers approve rule candidates, not raw model suggestions.

## Required before approval

- Target dimension and label
- Positive examples
- Negative examples
- Risk note
- Safety class
- Reviewer decision
- Rollback path

## Review decisions

- approve
- reject
- edit_and_approve
- needs_more_examples
- duplicate
- unsafe
- defer

## High-risk labels

Pricing, timeline, NDA, agreement, portfolio, payment, and marketing guarantee rules are high-risk. They require negative examples and shadow-mode observation before active promotion.
