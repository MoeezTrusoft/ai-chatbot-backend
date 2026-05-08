# D-082: Funnel Rule Governance After D-081

## Status

Accepted.

## Context

D-081 intentionally moved funnel-stage classification into Tri-Match, with the
funnel-stage vote launched in shadow mode and Decision Layer weight `0`.
Historical Phase 8 language described a separate Funnel Signal Engine. Keeping
that engine as an independent runtime classifier would create two owners for the
same funnel-stage signal.

## Decision

Phase 8 implements funnel rule governance and partitioning, not an independent
funnel-stage runtime. Imported funnel material is partitioned into:

- user-language rules that may be converted into Tri-Match `funnel_stage` rules;
- CRM/internal rules that are retained for later calibration but not consumed by
  chat classification;
- metadata-only or unsafe rules that are dropped by the verifier.

Tri-Match funnel-stage output remains shadow-only with Decision Layer weight `0`.
It must not directly mutate `ThreadState.sales_stage`.

## Consequences

- The `funnel_signal` package is retained as the governance boundary for funnel
  rule material.
- CRM and metadata rules are not consumed by the runtime chat loop in this phase.
- Any later specialized Funnel Signal sibling must be introduced by a new ADR and
  must not conflict with Tri-Match ownership of the shadow funnel-stage vote.
