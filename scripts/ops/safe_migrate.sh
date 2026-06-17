#!/usr/bin/env bash
#
# safe_migrate.sh — production-safe Alembic migration.
#
#   1. Resolves the live DATABASE_URL exactly as the app/Alembic see it.
#   2. Backs up the database (pg_dump, custom format) and VERIFIES the backup.
#   3. Runs `alembic upgrade head`.
#   4. Verifies the sales_* tables now exist.
#   5. On ANY failure during/after the migration, AUTOMATICALLY restores the
#      database to the pre-migration backup (drop+recreate schema, pg_restore)
#      and restarts the bot.
#
# Everything is logged to backups/logs/safe_migrate_<ts>.log.
#
# Usage (run ON the server):
#   cd /var/www/ai-chatbot-backend
#   bash scripts/ops/safe_migrate.sh             # backup -> migrate -> verify (auto-rollback on failure)
#   bash scripts/ops/safe_migrate.sh --dry-run   # backup + verify ONLY (no migration) — safe rehearsal
#
# Env overrides:  PM2_APP (default "10"),  BACKUP_DIR (default <repo>/backups)
#
set -Eeuo pipefail

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

# Resolve repo root from this script's own location (…/scripts/ops/safe_migrate.sh)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT"

TS="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
LOG_DIR="$BACKUP_DIR/logs"
mkdir -p "$BACKUP_DIR" "$LOG_DIR"
LOG="$LOG_DIR/safe_migrate_$TS.log"
DUMP="$BACKUP_DIR/db_backup_$TS.dump"
PM2_APP="${PM2_APP:-10}"
MIGRATION_STARTED=0
PG_URL=""

# Tee all output to the log file.
exec > >(tee -a "$LOG") 2>&1

log(){ echo "[$(date '+%F %T')] $*"; }
hr(){ printf '%.0s─' {1..72}; echo; }

restore_backup() {
  trap - ERR  # never recurse while rolling back
  if [[ ! -s "$DUMP" ]]; then
    log "FATAL: no usable backup at $DUMP — CANNOT auto-restore. MANUAL ACTION REQUIRED."
    return 1
  fi
  hr; log "ROLLBACK — restoring the database to the pre-migration backup"; hr
  log "Stopping bot (pm2 $PM2_APP) so the restore is clean…"
  pm2 stop "$PM2_APP" >/dev/null 2>&1 || log "warn: 'pm2 stop $PM2_APP' failed — continuing"
  log "Resetting schema 'public' (drop + recreate)…"
  if ! psql "$PG_URL" -v ON_ERROR_STOP=1 -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"; then
    log "FATAL: schema reset failed — DB may be inconsistent. Backup preserved at: $DUMP"
    pm2 start "$PM2_APP" >/dev/null 2>&1 || true
    return 1
  fi
  log "Restoring from $DUMP…"
  if pg_restore --no-owner --no-privileges -d "$PG_URL" "$DUMP"; then
    log "ROLLBACK OK — database restored to its pre-migration state."
  else
    log "WARNING: pg_restore reported errors. Inspect the DB manually; backup kept at: $DUMP"
  fi
  log "Restarting bot (pm2 $PM2_APP)…"
  pm2 start "$PM2_APP" >/dev/null 2>&1 || log "warn: 'pm2 start $PM2_APP' failed — START THE BOT MANUALLY"
}

on_err() {
  local line="${1:-?}"
  trap - ERR
  log "ERROR at line $line."
  if [[ "$MIGRATION_STARTED" == "1" ]]; then
    restore_backup || log "Automatic restore did not complete cleanly — review the log above."
  else
    log "Failure occurred BEFORE the migration began — the database is UNCHANGED. No restore needed."
  fi
  log "Full log: $LOG"
  exit 1
}
trap 'on_err $LINENO' ERR

hr; log "SAFE MIGRATE  ($TS)"; hr
log "Repo: $ROOT"
log "Log:  $LOG"
log "Tooling:"; uv run alembic --version 2>/dev/null || log "warn: alembic --version failed"
pg_dump --version; psql --version

# ── 1) Resolve the DB URL exactly as the app/alembic use it ──────────────────
log "Resolving DATABASE_URL from the app's settings…"
RAW_URL="$(uv run python -c 'from bookcraft.infra.config import get_settings; print(get_settings().database_url)')"
PG_URL="$(printf '%s' "$RAW_URL" | sed -E 's#\+asyncpg##; s#\+psycopg2?##')"
SAFE_URL="$(printf '%s' "$PG_URL" | sed -E 's#(://[^:/]+:)[^@]+@#\1****@#')"
log "Target database: $SAFE_URL"
case "$PG_URL" in
  postgresql://*|postgres://*) : ;;
  *) log "Resolved URL is not PostgreSQL — refusing to run. Aborting."; exit 2 ;;
esac
for bin in pg_dump pg_restore psql; do
  command -v "$bin" >/dev/null || { log "Missing required tool: $bin (install postgresql-client). Aborting."; exit 2; }
done

log "Proceeding in 5s — press Ctrl-C now to abort…"; sleep 5

# ── 2) Record current migration state ────────────────────────────────────────
hr; log "Pre-migration alembic revision:"; uv run alembic current || log "warn: 'alembic current' failed"
log "Recent migration history:"; uv run alembic history 2>/dev/null | tail -12 || true

# ── 3) Back up ───────────────────────────────────────────────────────────────
hr; log "Backing up database → $DUMP"
pg_dump --format=custom --no-owner --no-privileges -d "$PG_URL" -f "$DUMP"

# ── 4) Verify the backup ─────────────────────────────────────────────────────
log "Verifying backup integrity…"
[[ -s "$DUMP" ]] || { log "Backup file is empty/missing — aborting BEFORE migration."; exit 3; }
OBJ_COUNT="$(pg_restore --list "$DUMP" 2>/dev/null | grep -cE '^[0-9]+;' || true)"
log "Backup OK: size=$(du -h "$DUMP" | cut -f1), catalogued_objects=${OBJ_COUNT:-0}"
[[ "${OBJ_COUNT:-0}" -ge 1 ]] || { log "Backup catalogues 0 objects — refusing to migrate. Aborting."; exit 3; }

if [[ "$DRY_RUN" == "1" ]]; then
  hr; log "DRY RUN complete — backup taken & verified, NO migration performed."
  log "Backup: $DUMP"; trap - ERR; exit 0
fi

# ── 5) Migrate (auto-rollback armed from here) ───────────────────────────────
hr; log "Applying migrations:  uv run alembic upgrade head"
MIGRATION_STARTED=1
uv run alembic upgrade heads

# ── 6) Verify the result ─────────────────────────────────────────────────────
hr; log "Verifying sales_* tables exist…"
TABLES="$(psql "$PG_URL" -At -c "select tablename from pg_tables where schemaname='public' and tablename like 'sales_%' order by 1")"
if [[ -n "$TABLES" ]]; then printf '%s\n' "$TABLES" | sed 's/^/   - /'; else log "   (none found)"; fi
echo "$TABLES" | grep -qx "sales_leads"         || { log "VERIFY FAILED: sales_leads still missing";        on_err "$LINENO"; }
echo "$TABLES" | grep -qx "sales_consultations" || { log "VERIFY FAILED: sales_consultations still missing"; on_err "$LINENO"; }
log "Post-migration alembic revision:"; uv run alembic current || true

# ── Success ──────────────────────────────────────────────────────────────────
trap - ERR
hr
log "✅ SUCCESS — migrations applied and verified. Backup retained at:"
log "      $DUMP"
log "Manual rollback (only if ever needed):"
log "      pm2 stop $PM2_APP"
log "      psql '<DB_URL>' -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'"
log "      pg_restore --no-owner -d '<DB_URL>' '$DUMP'"
log "      pm2 start $PM2_APP"
hr
log "Next: python3 scripts/prod_consultation_smoke.py   # expect RESULT: 6/6"
