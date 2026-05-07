# BookCraft AI Chatbot

Production-grade AI sales assistant for BookCraft Publishers.

This repository is being implemented phase by phase from the locked architecture and implementation guide. Phase 0 contains the local foundation only: package setup, service scaffold, health/readiness endpoints, observability shell, Docker Compose, and CI skeleton.

## Local Commands

```bash
make install
make lint
make type
make test
make run
make up
make down
make smoke
```

## Phase 0 Endpoints

- `GET /healthz`
- `GET /readyz`
- `GET /metrics`

## Canonical Documents

- `docs/architecture/architecture-reference.md`
- `docs/architecture/architecture-amendments.md`
- `docs/implementation/bookcraft_ai_chatbot_ultimate_implementation_guide.md`

