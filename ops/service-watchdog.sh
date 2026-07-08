#!/usr/bin/env bash
#
# service-watchdog.sh — health-check the AI chatbot backend's required
# dependency containers and auto-restart any that are down or unresponsive.
#
# A stopped/unresponsive Redis (or Postgres/TEI/ES) makes /api/v1/chat/turn
# fail, which silently drops customer replies. Docker's `restart: unless-stopped`
# policy handles crashes and daemon restarts; this watchdog additionally catches
# the "container is Up but the service inside is hung" case, and is the belt to
# that policy's suspenders.
#
# Run every minute via the companion systemd timer (chatbot-watchdog.timer).
# Idempotent and safe to run repeatedly.

set -uo pipefail

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"; }

# container | liveness probe (exit 0 == healthy)
# The probe runs from the host; redis/postgres use `docker exec` so we depend on
# no host-side client tools, while tei/es probe their mapped localhost ports.
check_ai_redis() { docker exec ai-redis redis-cli ping 2>/dev/null | grep -q PONG; }
check_ai_postgres() { docker exec ai-postgres pg_isready -U bookcraft -d bookcraft >/dev/null 2>&1; }
check_ai_tei() {
  curl -fsS -m 5 -X POST http://localhost:8080/embed \
    -H 'Content-Type: application/json' -d '{"inputs":"ping"}' -o /dev/null 2>/dev/null
}
check_ai_elasticsearch() {
  # Accept green or yellow (single-node is yellow by design).
  curl -fsS -m 5 http://localhost:9200/_cluster/health 2>/dev/null \
    | grep -qE '"status":"(green|yellow)"'
}

CONTAINERS=(ai-redis ai-postgres ai-tei ai-elasticsearch)

restarted=0
for c in "${CONTAINERS[@]}"; do
  if ! docker inspect "$c" >/dev/null 2>&1; then
    log "WARN $c does not exist — skipping (create it via docker compose)"
    continue
  fi

  running=$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)
  if [ "$running" != "true" ]; then
    log "DOWN $c is not running — starting"
    docker start "$c" >/dev/null 2>&1 && log "OK started $c" || log "ERR failed to start $c"
    restarted=$((restarted + 1))
    continue
  fi

  # Container is Up — confirm the service inside actually responds.
  if "check_${c//-/_}"; then
    : # healthy
  else
    log "UNHEALTHY $c is Up but its service is not responding — restarting"
    docker restart "$c" >/dev/null 2>&1 && log "OK restarted $c" || log "ERR failed to restart $c"
    restarted=$((restarted + 1))
  fi
done

if [ "$restarted" -eq 0 ]; then
  log "OK all required services healthy"
fi
exit 0
