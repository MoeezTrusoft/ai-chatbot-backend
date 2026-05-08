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

## Deployment

CD is intentionally a skeleton until packaging and infrastructure are approved.
The workflow documents the expected staging path: build image, run migrations,
deploy staging, smoke test, then require a manual production gate.
