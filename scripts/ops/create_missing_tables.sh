#!/usr/bin/env bash
#
# create_missing_tables.sh — create model tables that no Alembic migration creates.
#
# This repo defines some tables only as SQLModel models (notably `sales_leads`,
# `sales_document_requests`, `sales_pricing_quotes`) with NO migration, so a
# migration-only production DB never gets them — every lead INSERT then fails with
# UndefinedTableError. SQLModel `create_all` fixes this: it is ADDITIVE and
# idempotent — it only CREATEs tables that don't exist and NEVER drops or alters
# existing tables or data. So there is no destructive step and no need for a
# drop+restore rollback; a backup is still taken purely as insurance.
#
# Usage (run ON the server):
#   cd /var/www/ai-chatbot-backend
#   bash scripts/ops/create_missing_tables.sh
#
# Env overrides:  BACKUP_DIR (default <repo>/backups)
#
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT"

TS="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
LOG_DIR="$BACKUP_DIR/logs"
mkdir -p "$BACKUP_DIR" "$LOG_DIR"
LOG="$LOG_DIR/create_missing_tables_$TS.log"
DUMP="$BACKUP_DIR/db_backup_$TS.dump"
PG_URL=""

exec > >(tee -a "$LOG") 2>&1
log(){ echo "[$(date '+%F %T')] $*"; }
hr(){ printf '%.0s─' {1..72}; echo; }

on_err() {
  local line="${1:-?}"
  trap - ERR
  log "ERROR at line $line."
  log "create_all is additive — it never drops/alters existing tables, so your"
  log "existing data is intact. No automatic restore performed."
  [[ -s "$DUMP" ]] && log "Backup (insurance) is at: $DUMP"
  log "Full log: $LOG"
  exit 1
}
trap 'on_err $LINENO' ERR

hr; log "CREATE MISSING TABLES  ($TS)"; hr
log "Repo: $ROOT"; log "Log:  $LOG"
pg_dump --version; psql --version

# ── Resolve DB URL exactly as the app sees it ────────────────────────────────
log "Resolving DATABASE_URL from app settings…"
RAW_URL="$(uv run python -c 'from bookcraft.infra.config import get_settings; print(get_settings().database_url)')"
PG_URL="$(printf '%s' "$RAW_URL" | sed -E 's#\+asyncpg##; s#\+psycopg2?##')"
SAFE_URL="$(printf '%s' "$PG_URL" | sed -E 's#(://[^:/]+:)[^@]+@#\1****@#')"
log "Target database: $SAFE_URL"
case "$PG_URL" in postgresql://*|postgres://*) : ;; *) log "Not a PostgreSQL URL — aborting."; exit 2 ;; esac
for bin in pg_dump psql; do command -v "$bin" >/dev/null || { log "Missing tool: $bin (install postgresql-client). Aborting."; exit 2; }; done

# ── Pre-flight: confirm the DB is intact (e.g. after a prior rollback) ────────
hr; log "Pre-flight health check…"
CORE="$(psql "$PG_URL" -At -c "select count(*) from pg_tables where schemaname='public' and tablename in ('threads','customers','sales_consultations')")"
log "core tables present: ${CORE}/3 (threads, customers, sales_consultations)"
[[ "$CORE" == "3" ]] || { log "Core tables are MISSING — the database looks incomplete."; \
  log "If a prior run left it broken, restore the newest backups/db_backup_*.dump before continuing. Aborting."; exit 3; }
log "Alembic revision(s):"; uv run alembic current 2>/dev/null || log "warn: alembic current failed"

log "sales_* tables BEFORE:"
psql "$PG_URL" -At -c "select tablename from pg_tables where schemaname='public' and tablename like 'sales_%' order by 1" | sed 's/^/   - /' || true

# ── Backup (insurance only) ──────────────────────────────────────────────────
hr; log "Backing up database → $DUMP"
pg_dump --format=custom --no-owner --no-privileges -d "$PG_URL" -f "$DUMP"
[[ -s "$DUMP" ]] || { log "Backup empty — aborting before any change."; exit 3; }
OBJ="$(pg_restore --list "$DUMP" 2>/dev/null | grep -cE '^[0-9]+;' || true)"
log "Backup OK: size=$(du -h "$DUMP" | cut -f1), objects=${OBJ:-0}"
[[ "${OBJ:-0}" -ge 1 ]] || { log "Backup catalogues 0 objects — aborting."; exit 3; }

# ── Create missing tables (additive, idempotent) ─────────────────────────────
hr; log "Running create_all (CREATE IF NOT EXISTS for every model table)…"
uv run python - <<'PY'
import asyncio
import bookcraft.components.storage.models  # noqa: F401 — register all tables in metadata
from bookcraft.components.storage.db import create_engine, create_all
from bookcraft.infra.config import get_settings

asyncio.run(create_all(create_engine(get_settings())))
print("create_all completed")
PY

# ── Verify ───────────────────────────────────────────────────────────────────
hr; log "sales_* tables AFTER:"
TABLES="$(psql "$PG_URL" -At -c "select tablename from pg_tables where schemaname='public' and tablename like 'sales_%' order by 1")"
printf '%s\n' "$TABLES" | sed 's/^/   - /'
echo "$TABLES" | grep -qx "sales_leads"         || { log "VERIFY FAILED: sales_leads still missing"; on_err "$LINENO"; }
echo "$TABLES" | grep -qx "sales_consultations" || { log "VERIFY FAILED: sales_consultations missing"; on_err "$LINENO"; }

trap - ERR
hr
log "✅ SUCCESS — missing tables created. Existing data was untouched (additive)."
log "Backup (insurance) retained at: $DUMP"
log "No bot restart needed — the next lead/consultation write will now succeed."
hr
log "Next: python3 scripts/prod_consultation_smoke.py   # expect RESULT: 6/6"
