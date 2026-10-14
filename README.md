# BookCraft AI Chatbot

Production-grade AI sales assistant for BookCraft Publishers.

This repository is being implemented phase by phase from the locked architecture and implementation guide. It now includes the local app shell, guarded chat loop, RAG verifier/retriever, deterministic pricing engine, portfolio registry, template-only document generation, Tri-Match with ADR-gated funnel-stage shadow output, monitoring assets, governance gates, and final acceptance coverage.

## Local Docker Security Defaults

`docker-compose.yml` is for local development only. By default, infrastructure ports are bound to `127.0.0.1` through `DOCKER_BIND_HOST` so Postgres, Redis, Elasticsearch, TEI, Prometheus, Grafana, Loki, and OTEL are not exposed on the LAN.

Keep this default unless you intentionally need controlled lab access. Do not reuse the local Compose file for staging or production without service authentication, TLS, secret management, network isolation, and hardened deployment configuration.

Grafana uses `GRAFANA_ADMIN_PASSWORD` from `.env` / `.env.example`. Change it before sharing a running environment.


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
make chat-diagnostics
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
- `POST /mcp` — only mounted when `CHATBOT_MCP_SERVER_ENABLED=true`

## Sales integration (`erp-sales-backend`)

Both directions of the bridge are off by default and configured only through the environment; see
the Sales MCP block in `.env.example` (`.env.staging.example` for staging).

- **Bot → Sales (MCP `tools/call`).** With `SALES_MCP_ENABLED=true` the bot writes lead,
  chatroom, message and consultation state through Sales' MCP endpoint, signing every call with an
  Ed25519 service-JWT (`SALES_MCP_ISSUER`/`SALES_MCP_KID`). Sales must hold the matching public key
  in `SERVICE_JWT_PUBLIC_KEYS_JSON` and the issuer in `SERVICE_JWT_EDDSA_ISSUERS`. Only sites listed
  in `SALES_MCP_TENANT_ALLOWLIST_JSON` are served. A failed or unauthorized call degrades to the
  local path rather than losing the lead. `CHATBOT_LOCAL_SCHEDULER=true` keeps the in-process
  dispatcher + CSR push for standalone/dev; production sets it false so Sales owns the booking.
- **Sales → bot (MCP server, C4).** With `CHATBOT_MCP_SERVER_ENABLED=true` the bot also serves its
  own read-mostly tools (`chat.turn`, `chat.greet`, `chat.state`, `pricing.quote`,
  `portfolio.lookup`, `documents.render`) over `POST /mcp`, authenticated with the same Ed25519
  service-token shape via `CHATBOT_MCP_PUBLIC_KEYS_JSON` / `CHATBOT_MCP_EDDSA_ISSUERS`. The tool
  manifest is frozen in `erp-contracts/src/mcp/chatbot.js` and mirrored by
  `tests/unit/test_chatbot_mcp_contract_parity.py`.

End-to-end proof of the token seam:

```bash
PYTHONPATH=src .venv/bin/python ../../deployments/chatbot-sales-20261012/verify-bot-token-crossrepo.py > /tmp/bot-token.json
cd ../erp-sales-backend && node scripts/verify-bot-token-crossrepo.mjs /tmp/bot-token.json
```

## Acceptance

Run the local acceptance path with:

```bash
make acceptance
```

This exercises the Phase 14 customer journey in process: ghostwriting inquiry, pricing/timeline clarification, gated pricing fallback, registry-backed portfolio samples, ghostwriting confidentiality, NDA/agreement routing, conversation event inspection, and strict-template document rendering.

Pricing v2.2 values are gated by default. The chatbot must not emit customer-facing pricing or timeline numbers unless approved values are explicitly enabled through configuration.

See `docs/runbooks/final-acceptance.md` for the release checklist and current constraints.
See `docs/runbooks/complex-chat-testing-and-ai-keys.md` for complex test messages and live AI key setup.

For a deeper 25-turn diagnostic report after the API is running:

```bash
make chat-diagnostics
```

JSON, Markdown, and Word `.docx` reports are written to `reports/chat-diagnostics/`.

## Canonical Documents

- `docs/architecture/architecture-reference.md`
- `docs/architecture/architecture-amendments.md`
- `docs/implementation/bookcraft_ai_chatbot_ultimate_implementation_guide.md`
