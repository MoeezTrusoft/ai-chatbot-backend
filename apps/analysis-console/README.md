# BookCraft AI Analysis Console — Production One-Stop Shop

A production-ready internal console for analyzing and governing the BookCraft AI chatbot.

This build supports both workflows:

1. **Report mode** — upload JSON reports locally for safe review.
2. **Live admin API mode** — call FastAPI admin endpoints, mutate rule candidates, run evals, run Rules Army v2 preflight, activate v2 with backup/audit, and rollback.

## Features

- Executive dashboard
- Conversation trace viewer
- Component latency waterfall
- Intent decision inspector
- Provider vote comparison
- Runtime context atoms panel
- Tri-Match evidence explorer
- Rules Army v2 activation center
- LLM rule candidate approval workflow
- Context eval runner
- Safety and response-quality panels
- Pricing/RAG trace shells
- Admin API configuration
- LocalStorage persistence
- Production build verified

## Run locally

```bash
cd apps/analysis-console
npm install
npm run dev
```

Open:

```text
http://localhost:5173
```

## Production build

```bash
npm run build
npm run preview
```

## Enable backend API mode

1. Copy included backend files into the repo root if not already present.
2. Configure:

```bash
export BOOKCRAFT_ADMIN_ANALYSIS_TOKEN="replace-with-long-random-secret"
uv run python scripts/admin/enable_analysis_console_routes.py
```

3. Restart FastAPI.
4. In the console, open **Settings**:

```text
Enable live admin API: checked
Base URL: http://localhost:8000
Bearer token: same as BOOKCRAFT_ADMIN_ANALYSIS_TOKEN
```

## Rules Army v2 activation

The console includes a dedicated activation center:

1. Run preflight.
2. Review verifier/context status.
3. Enter confirm phrase:

```text
I_UNDERSTAND_THIS_PROMOTES_RULES_ARMY_V2
```

4. Choose `active replacement` or `shadow record only`.
5. Activate with backup.
6. Run context eval.
7. Roll back if needed.

If formal verifier is red, active promotion is blocked unless `force=true` is checked. Use force only after manual approval.
