# D-081: Tri-Match Funnel-Stage Classification in Shadow Mode

**Status:** Approved for implementation

## Decision

Tri-Match now classifies three dimensions: query intent, service intent, and funnel stage.
Its funnel-stage vote launches in shadow mode with Decision Layer weight `0`.

## Context

D-070 introduced a separate Funnel Signal Engine because deterministic funnel-stage rules were
contextual and should not directly mutate sales stage. The implementation plan for Phase 7 now
intentionally brings funnel-stage classification into Tri-Match while preserving the original
safety property: Tri-Match may emit a funnel-stage vote, but it does not own final stage decisions.

## Consequences

- Tri-Match emits `query_intent`, `service_intent`, and `funnel_stage` evidence.
- Funnel-stage evidence is tagged separately from query/service evidence.
- Phase 7 must not mutate `ThreadState.sales_stage` from Tri-Match output.
- Funnel-stage Decision Layer weight remains `0` until calibrated in a later phase.
- Funnel Signal Engine is deferred unless later retained as a specialized sibling component.
