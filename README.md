# BookCraft AI Chatbot

Production-grade AI sales assistant for BookCraft Publishers.

This repository is being implemented phase by phase from the locked architecture and implementation guide. It now includes the local app shell, guarded chat loop, RAG verifier/retriever, deterministic pricing engine, portfolio registry, template-only document generation, Tri-Match with ADR-gated funnel-stage shadow output, monitoring assets, governance gates, and final acceptance coverage.

## Local Commands

```bash
make install
make lint
make type
make test
make up
make migrate
make run
make down
make smoke
make acceptance
make chat-probe
make verifier-gates
make ci-local
```

Docker Compose maps Postgres to host port `55432` by default to avoid conflicts with a local Postgres on `5432`. Host-side tools should use:

```bash
DATABASE_URL=postgresql+asyncpg://bookcraft:bookcraft_dev@localhost:55432/bookcraft
```

## Endpoints

- `GET /healthz`
- `GET /readyz`
- `GET /metrics`
- `POST /api/v1/chat/turn`
- `WS /api/v1/chat/ws/{thread_id}`

## Acceptance

Run the local acceptance path with:

```bash
make acceptance
```

This exercises the Phase 14 customer journey in process: ghostwriting inquiry, pricing/timeline clarification, gated pricing fallback, registry-backed portfolio samples, ghostwriting confidentiality, NDA/agreement routing, conversation event inspection, and strict-template document rendering.

Pricing v2.2 values are gated by default. The chatbot must not emit customer-facing pricing or timeline numbers unless approved values are explicitly enabled through configuration.

See `docs/runbooks/final-acceptance.md` for the release checklist and current constraints.
See `docs/runbooks/complex-chat-testing-and-ai-keys.md` for complex test messages and live AI key setup.

## Canonical Documents

- `docs/architecture/architecture-reference.md`
- `docs/architecture/architecture-amendments.md`
- `docs/implementation/bookcraft_ai_chatbot_ultimate_implementation_guide.md`
