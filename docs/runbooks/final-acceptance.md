# Final Acceptance Runbook

Phase 14 validates the implemented BookCraft chatbot as an end-to-end local system while preserving the architecture gates.

## Local Acceptance

Run:

```bash
make acceptance
make test
make verifier-gates
docker compose config
```

The local Docker stack maps Postgres to host port `55432` by default. Use
`POSTGRES_HOST_PORT=<free-port>` to override it when needed. Container-to-container
traffic still uses Postgres port `5432`.

The acceptance script runs the chat loop in process with deterministic test settings. It covers:

- initial ghostwriting inquiry,
- pricing and timeline request with missing-input clarification,
- gated pricing fallback when production v2.2 values are not approved,
- portfolio sample retrieval from the approved registry,
- ghostwriting confidentiality behavior,
- NDA request routing to template-gated status,
- service agreement request routing to accepted-quote/template-gated status,
- conversation event inspection for Tri-Match, intent classification, and assistant response records,
- direct NDA and service agreement template rendering and verification.

## Required Gates

- RAG verifier must pass with no pricing or timeline leakage.
- Pricing verifier must report the production approval state and fail closed for unapproved formal quotes.
- Portfolio verifier must reject broken or empty sample records.
- Document verifier must reject unknown template variables and unapproved template behavior.
- Tri-Match funnel-stage output remains shadow-only with Decision Layer weight `0`.
- Funnel governance rules must not become a second runtime funnel owner.
- Security and dependency scans must pass before release.

## Current Release Constraints

- Pricing v2.2 values are imported but not customer-approved by default, so local chat must not emit customer-facing quote numbers unless `PRICING_V2_VALUES_APPROVED=true` is intentionally enabled with approved business values.
- Document generation is available through the strict template engine and dispatcher gating. Chat routing only starts the request and reports required fields/status.
- Local tests use in-memory thread state and audit sinks. Production durable quote/document/audit persistence remains a deployment hardening item.
