# Production Deployment Guide — BookCraft Analysis Console

## 1. Install frontend

```bash
cd apps/analysis-console
npm install
npm run build
```

Serve `apps/analysis-console/dist` behind NGINX or any static host.

## 2. Install backend admin routes

The package includes:

```text
src/bookcraft/api/admin_analysis.py
scripts/admin/enable_analysis_console_routes.py
```

Enable route inclusion:

```bash
export BOOKCRAFT_ADMIN_ANALYSIS_TOKEN="replace-with-long-random-secret"
uv run python scripts/admin/enable_analysis_console_routes.py
uv run ruff check src/bookcraft/api/admin_analysis.py src/bookcraft/api/main.py --fix
uv run mypy src
```

Restart FastAPI.

## 3. Security checklist

- Use HTTPS only.
- Set a long random `BOOKCRAFT_ADMIN_ANALYSIS_TOKEN`.
- Put the console behind VPN/IP allowlist if possible.
- Never expose the admin API publicly without auth.
- Review activation audit log after every mutation:

```text
data/trimatch/activation_log.jsonl
```

## 4. Rules Army v2 activation checklist

- CI green on main.
- Context report is 8/8.
- Formal verifier reviewed.
- Run preflight in console.
- Confirm backup path is generated.
- Activate.
- Restart/reload API process if rule packs are loaded at startup.
- Run production component report.
- If regression: rollback using backup path.
