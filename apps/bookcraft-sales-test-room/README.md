# BookCraft Sales Test Room

A standalone light-theme WhatsApp-style tester for BookCraft chatbot sales reviews.

Theme colors:

- Background: `#F7F5EE`
- Accent: `#c2410c`

## Why users do not type tokens

Real website visitors should never paste a Customer ID, chat token, or admin token.

This app now works the right way:

1. The browser creates an anonymous customer UUID automatically and stores it in localStorage under `bookcraft.salesTestRoom.customerId.v1`.
2. The frontend asks a **server-side session service** for a temporary chat JWT.
3. The admin analysis token is never shown to customers. Optional trace review can be proxied by the session service for internal testing.

Do not generate production JWTs directly in browser code. That would expose `JWT_SIGNING_KEY` to every visitor.

## Run the BookCraft backend

From your backend repo:

```bash
cd /Users/mac/Desktop/Abdullah/ai_chatbot
source /Users/mac/Desktop/Abdullah/ai_chatbot/.venv/bin/activate

set -a
source .env.production.local
set +a

uv run uvicorn bookcraft.api.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --reload \
  --log-level debug
```

Backend URL:

```text
http://localhost:8000
```

## Run the local session service

This is for local/internal testing only. It reads `JWT_SIGNING_KEY` and `BOOKCRAFT_ADMIN_ANALYSIS_TOKEN` from `.env.production.local`, signs temporary chat JWTs server-side, and optionally proxies traces.

From this app folder:

```bash
npm run session-server
```

If your env file is somewhere else:

```bash
ENV_FILE=/Users/mac/Desktop/Abdullah/ai_chatbot/.env.production.local npm run session-server
```

Session service URL:

```text
http://localhost:8787
```

Endpoints:

```text
GET /api/session
GET /api/session?customer_id=<customer_uuid>
GET /api/traces/<thread_uuid>?limit=20
GET  /health
```

`/api/session` returns only the customer UUID, a temporary chat JWT, and its expiration. The service keeps `JWT_SIGNING_KEY` and `BOOKCRAFT_ADMIN_ANALYSIS_TOKEN` server-side.

## Run the frontend

```bash
npm install
npm run dev
```

Open:

```text
http://localhost:5174
```

Default app settings:

```text
API URL: http://localhost:8000
Session service URL: http://localhost:8787
```

The sales team should not need to paste tokens.

## Production website integration

For the real website, implement a server endpoint in your website/backend like:

```text
GET /api/chat/session?customer_id=<customer_uuid>
```

It should:

1. Create or reuse a customer ID.
2. Sign a short-lived JWT using `JWT_SIGNING_KEY` on the server only.
3. Return:

```json
{
  "customer_id": "2b5a7d2e-6c31-46ce-9b64-4c8c3a9c90d6",
  "chat_token": "jwt-token",
  "expires_at": 1770000000
}
```

Then configure this frontend or your website widget to call that endpoint before sending chat messages.

Chat turns are sent to the backend as:

```json
{
  "message": "I need ghostwriting help for my fantasy novel.",
  "customer_id": "2b5a7d2e-6c31-46ce-9b64-4c8c3a9c90d6",
  "thread_id": "39f7b37d-2ec7-4f45-8d3a-63d1783e9194"
}
```

The request body never includes `chat_token`, `admin_token`, `token`, `base_url`, or nested session objects. Auth is sent only in the `Authorization` header.

## What sales reviewers see

The right panel translates technical chatbot state into plain English:

- Customer need
- Service detected
- Sales stage
- Confidence
- Bot source
- Action taken
- Missing information
- Detected details
- Suggested next step

Raw response and raw trace are still available in collapsible sections for QA.
