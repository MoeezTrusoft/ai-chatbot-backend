# Complex Chat Testing and AI Keys

This runbook covers local complex-message testing and AI provider key setup.

## Safe Default

The default mode is deterministic and local:

```bash
LLM_PROVIDER_MODE=mock
```

Use mock mode first when validating pricing, portfolio, document, RAG, and routing safety. Mock mode still exercises preprocessing, Tri-Match shadow votes, extraction, pricing gates, portfolio registry behavior, document-status routing, event logging, and metrics.

## Start Local Services

```bash
make up
make migrate
make run
```

Postgres is exposed on host port `55432` by default:

```bash
DATABASE_URL=postgresql+asyncpg://bookcraft:bookcraft_dev@localhost:55432/bookcraft
```

If TEI is unavailable, local chat uses degraded embeddings when `TEI_DEGRADED_MODE_ENABLED=true`.

## Run Complex Chat Probe

In another terminal:

```bash
make chat-probe
```

The probe sends multi-intent messages covering:

- ghostwriting, editing, and cover design scoping,
- contact extraction,
- pricing and timeline requests with no customer-facing number leakage,
- ghostwriting sample confidentiality,
- document-template gating for NDA and service agreement requests.

For another server URL:

```bash
BOOKCRAFT_CHAT_BASE_URL=http://localhost:8000 make chat-probe
```

For full responses:

```bash
UV_CACHE_DIR=.uv-cache python3 -m uv run python scripts/dev/complex_chat_probe.py --json
```

## Add AI API Keys

Create a local `.env` from the example and fill only local secrets:

```bash
cp .env.example .env
```

Set:

```bash
LLM_PROVIDER_MODE=live
ANTHROPIC_API_KEY=your_anthropic_key
ANTHROPIC_BASE_URL=https://api.anthropic.com
ANTHROPIC_HAIKU_MODEL=claude-haiku-4-5
ANTHROPIC_SONNET_MODEL=claude-sonnet-4-5

OPENAI_API_KEY=your_openai_key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_INTENT_MODEL=gpt-5.4-mini

DEEPSEEK_BASE_URL=http://your-deepseek-host:8000/v1
DEEPSEEK_API_KEY=
DEEPSEEK_INTENT_MODEL=deepseek-chat
```

Do not commit `.env`. Run `make security-scan` before committing any config changes.

## What Live Mode Does Today

Live mode currently applies to the three intent-classification votes:

- Claude Haiku,
- OpenAI GPT-5.4 mini,
- self-hosted DeepSeek-compatible endpoint.

The rest of the architecture remains gated:

- pricing and timelines still come only from the deterministic Pricing & Timeline Engine,
- portfolio links still come only from the approved registry,
- NDA and agreement text still renders only from approved templates,
- Tri-Match funnel-stage output remains shadow-only with Decision Layer weight `0`.

## Manual Test Messages

Use these after `make run`:

```bash
curl -sS http://localhost:8000/api/v1/chat/turn \
  -H 'content-type: application/json' \
  -d '{"message":"I need ghostwriting, editing, and cover design for a 76000 word fantasy manuscript. I have a partial draft, need an NDA, and my email is avery.author@example.com. What do you need first?"}'
```

```bash
curl -sS http://localhost:8000/api/v1/chat/turn \
  -H 'content-type: application/json' \
  -d '{"message":"Can you give me price, timeline, discount, portfolio samples, and a service agreement now?"}'
```

Expected safety behavior:

- no invented price or timeline numbers,
- no legal clauses drafted by an LLM,
- no unapproved sample links,
- clarification requested when quote fields are missing,
- document requests routed to template-gated status.
