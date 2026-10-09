# Security and Governance Runbook

## Local Gates

Run the full local CI gate before opening a pull request:

```bash
make ci-local
```

This runs linting, type checking, tests, all verifier gates, secret scanning,
dependency scanning, and Docker Compose configuration validation.

## Secret Handling

- Do not commit live credentials.
- Keep local credentials in `.env`, which is ignored by git.
- `.env.example` may contain empty placeholders only.
- Run `make security-scan` before committing.

## Verifier Gates

The repository fails closed for:

- RAG pricing/timeline leakage.
- Pricing config placeholders and invalid pricing rules.
- Portfolio records outside the approved registry.
- Document templates with unresolved or unsafe template constructs.
- Tri-Match and funnel rule validation failures.
- Prompt pack pricing, timeline, legal, or sample hallucination instructions.
- Eval corpus schema errors.

## Image Validation and Deployment Handoff

The image workflow builds a non-root container, runs health/readiness and migration
head smoke checks, and exports the image as a seven-day GitHub Actions artifact.
It does not publish to a registry or deploy; deployment is managed separately.

The deployment system must apply the database migration and validate authenticated
chat and CSR integration before routing traffic. Follow
[backend hardening and rollout](../backend-hardening.md), especially token scopes,
legacy conversation ownership and the receiver's idempotency contract.
