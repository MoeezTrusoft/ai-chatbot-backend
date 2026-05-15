# BookCraft Analysis Console Backend Integration

This package includes a production-ready admin API router for the Analysis Console.

## Files added

```text
src/bookcraft/api/admin_analysis.py
scripts/admin/enable_analysis_console_routes.py
apps/analysis-console/
```

## Enable routes

From the repo root:

```bash
export BOOKCRAFT_ADMIN_ANALYSIS_TOKEN="replace-with-long-random-secret"
uv run python scripts/admin/enable_analysis_console_routes.py
uv run ruff check src/bookcraft/api/admin_analysis.py src/bookcraft/api/main.py --fix
uv run mypy src
uv run pytest -q tests/unit/test_trimatch_engine.py tests/unit/test_preprocessor_context_atoms.py
```

Then restart FastAPI.

## Frontend settings

Open the console and configure:

```text
Base URL: http://localhost:8000
Bearer token: same value as BOOKCRAFT_ADMIN_ANALYSIS_TOKEN
Enable live admin API: checked
```

## Mutation endpoints

```text
GET  /api/admin/analysis/health
GET  /api/admin/analysis/reports/production
GET  /api/admin/analysis/reports/trimatch-context
POST /api/admin/analysis/evals/context-candidate/run
GET  /api/admin/analysis/rules/active
GET  /api/admin/analysis/rules/candidates
POST /api/admin/analysis/rules/candidates
PATCH /api/admin/analysis/rules/candidates/{candidate_id}
POST /api/admin/analysis/rules-army-v2/preflight
POST /api/admin/analysis/rules-army-v2/activate
POST /api/admin/analysis/rules-army-v2/rollback
```

## Rules Army v2 activation behavior

Active activation performs these steps:

1. Runs preflight.
2. Requires confirm phrase `I_UNDERSTAND_THIS_PROMOTES_RULES_ARMY_V2`.
3. Backs up current `data/trimatch/rules/*.json` into `data/trimatch/backups/<timestamp>_rules`.
4. Copies filtered v2 candidate rules into `data/trimatch/rules`.
5. Writes an audit event to `data/trimatch/activation_log.jsonl`.

If the formal verifier is red, activation is blocked unless `force=true` is sent. Use force only after manual approval.

## Rollback

Use the rollback endpoint or copy backup files back into `data/trimatch/rules`.
