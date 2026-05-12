#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-.env}"
BASE_URL="${BASE_URL:-http://localhost:8000}"
START_API="${START_API:-true}"
API_LOG="${API_LOG:-/tmp/bookcraft-production-flow-api.log}"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: env file not found: $ENV_FILE"
  exit 1
fi

echo "==> Load env safely from $ENV_FILE"
eval "$(
  python3 - "$ENV_FILE" <<'PY'
from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

path = Path(sys.argv[1])
key_pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

for raw_line in path.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue

    key, value = line.split("=", 1)
    key = key.strip()
    value = value.strip().strip('"').strip("'")

    if not key_pattern.match(key):
        continue

    print(f"export {key}={shlex.quote(value)}")
PY
)"

echo "==> Production mode checks"
test "${APP_ENV:-}" = "production" || { echo "ERROR: APP_ENV must be production"; exit 1; }
test "${API_AUTH_MODE:-}" = "jwt" || { echo "ERROR: API_AUTH_MODE must be jwt"; exit 1; }
test -n "${JWT_SIGNING_KEY:-}" || { echo "ERROR: JWT_SIGNING_KEY missing"; exit 1; }
test "${METRICS_PUBLIC:-}" = "false" || { echo "ERROR: METRICS_PUBLIC must be false"; exit 1; }

# The app's default production rate limit is intentionally strict.
# This 50-message diagnostic sends all turns quickly from localhost, so the runner
# raises the limit only for this local production-flow test process.
export RATE_LIMIT_PER_IP_PER_MINUTE="${PRODUCTION_FLOW_RATE_LIMIT_PER_MINUTE:-120}"
echo "RATE_LIMIT_PER_IP_PER_MINUTE=$RATE_LIMIT_PER_IP_PER_MINUTE"

echo "==> Docker must be running"
docker info >/dev/null

echo "==> Resolve Redis host port"
PREFERRED_REDIS_HOST_PORT="${REDIS_HOST_PORT:-6379}"
SELECTED_REDIS_HOST_PORT=""

for candidate in "$PREFERRED_REDIS_HOST_PORT" 6380 6381 6382 6383 6384 6385; do
  if ! lsof -nP -iTCP:"$candidate" -sTCP:LISTEN >/dev/null 2>&1; then
    SELECTED_REDIS_HOST_PORT="$candidate"
    break
  fi
done

if [ -z "$SELECTED_REDIS_HOST_PORT" ]; then
  echo "ERROR: no free Redis host port found in 6379-6385 range"
  lsof -nP -iTCP -sTCP:LISTEN | grep -E ':(6379|6380|6381|6382|6383|6384|6385)' || true
  exit 1
fi

if [ "$SELECTED_REDIS_HOST_PORT" != "$PREFERRED_REDIS_HOST_PORT" ]; then
  echo "Redis port $PREFERRED_REDIS_HOST_PORT is busy; using $SELECTED_REDIS_HOST_PORT"
fi

export REDIS_HOST_PORT="$SELECTED_REDIS_HOST_PORT"
export REDIS_URL="redis://localhost:${REDIS_HOST_PORT}/0"
echo "REDIS_HOST_PORT=$REDIS_HOST_PORT"
echo "REDIS_URL=$REDIS_URL"

echo "==> Resolve Elasticsearch host port"
PREFERRED_ELASTICSEARCH_HOST_PORT="${ELASTICSEARCH_HOST_PORT:-9200}"
SELECTED_ELASTICSEARCH_HOST_PORT=""

# Prefer the requested port if it is already a reachable Elasticsearch endpoint.
if curl -fsS "http://localhost:${PREFERRED_ELASTICSEARCH_HOST_PORT}/_cluster/health" >/dev/null 2>&1; then
  SELECTED_ELASTICSEARCH_HOST_PORT="$PREFERRED_ELASTICSEARCH_HOST_PORT"
else
  for candidate in "$PREFERRED_ELASTICSEARCH_HOST_PORT" 9201 9202 9203 9204 9205; do
    if ! lsof -nP -iTCP:"$candidate" -sTCP:LISTEN >/dev/null 2>&1; then
      SELECTED_ELASTICSEARCH_HOST_PORT="$candidate"
      break
    fi
  done
fi

if [ -z "$SELECTED_ELASTICSEARCH_HOST_PORT" ]; then
  echo "ERROR: no free Elasticsearch host port found in 9200-9205 range"
  lsof -nP -iTCP -sTCP:LISTEN | grep -E ':(9200|9201|9202|9203|9204|9205)' || true
  exit 1
fi

if [ "$SELECTED_ELASTICSEARCH_HOST_PORT" != "$PREFERRED_ELASTICSEARCH_HOST_PORT" ]; then
  echo "Elasticsearch port $PREFERRED_ELASTICSEARCH_HOST_PORT is unavailable; using $SELECTED_ELASTICSEARCH_HOST_PORT"
fi

export ELASTICSEARCH_HOST_PORT="$SELECTED_ELASTICSEARCH_HOST_PORT"
export ELASTICSEARCH_URL="http://localhost:${ELASTICSEARCH_HOST_PORT}"
echo "ELASTICSEARCH_HOST_PORT=$ELASTICSEARCH_HOST_PORT"
echo "ELASTICSEARCH_URL=$ELASTICSEARCH_URL"

echo "==> Recreate Redis and Elasticsearch containers with selected host ports"
docker compose rm -sf redis elasticsearch >/dev/null 2>&1 || true

echo "==> Start production dependencies"
REDIS_HOST_PORT="$REDIS_HOST_PORT" \
REDIS_URL="$REDIS_URL" \
ELASTICSEARCH_HOST_PORT="$ELASTICSEARCH_HOST_PORT" \
ELASTICSEARCH_URL="$ELASTICSEARCH_URL" \
docker compose up -d postgres redis elasticsearch tei prometheus grafana loki otel-collector

echo "==> Wait for Postgres"
for _ in $(seq 1 90); do
  if docker exec ai_chatbot-postgres-1 pg_isready -U bookcraft -d bookcraft >/dev/null 2>&1; then
    echo "postgres ok"
    break
  fi
  sleep 1
done

echo "==> Wait for Redis"
for _ in $(seq 1 60); do
  if docker exec ai_chatbot-redis-1 redis-cli ping >/dev/null 2>&1; then
    echo "redis ok"
    break
  fi
  sleep 1
done

if [ "$BASE_URL" = "http://localhost:8000" ] && [ "${RESET_LOCAL_REDIS_DB:-true}" = "true" ]; then
  echo "==> Reset local Redis DB for clean production-flow run"
  docker exec ai_chatbot-redis-1 redis-cli FLUSHDB >/dev/null
fi

echo "==> Wait for Elasticsearch"
for _ in $(seq 1 120); do
  if curl -fsS "$ELASTICSEARCH_URL/_cluster/health" >/tmp/bookcraft-es-health.json 2>/dev/null; then
    cat /tmp/bookcraft-es-health.json
    echo
    break
  fi
  sleep 1
done

if ! curl -fsS "$ELASTICSEARCH_URL/_cluster/health" >/tmp/bookcraft-es-health.json 2>/dev/null; then
  echo "ERROR: Elasticsearch is not reachable at $ELASTICSEARCH_URL"
  docker compose ps elasticsearch || true
  docker compose logs --tail=100 elasticsearch || true
  exit 1
fi

echo "==> Run migrations"
uv run alembic upgrade head

echo "==> Build and verify RAG"
make rag-build UV='uv'
make rag-verify UV='uv'

echo "==> Index RAG into Elasticsearch"
make rag-index UV='uv'

echo "==> Verifier gates"
make verifier-gates UV='uv'

if [ "$START_API" = "true" ]; then
  echo "==> Restart API on port 8000"
  if lsof -ti tcp:8000 >/tmp/bookcraft-api-pids 2>/dev/null; then
    xargs kill < /tmp/bookcraft-api-pids || true
    sleep 2
  fi

  rm -f "$API_LOG"
  nohup uv run uvicorn bookcraft.api.main:app \
    --host 127.0.0.1 \
    --port 8000 \
    >"$API_LOG" 2>&1 &

  echo $! >/tmp/bookcraft-production-flow-api.pid
fi

echo "==> Wait for API readiness: $BASE_URL"
for _ in $(seq 1 90); do
  if curl -fsS "$BASE_URL/readyz" >/tmp/bookcraft-production-flow-readyz.json 2>/dev/null; then
    cat /tmp/bookcraft-production-flow-readyz.json
    echo
    break
  fi
  sleep 1
done

if ! curl -fsS "$BASE_URL/readyz" >/tmp/bookcraft-production-flow-readyz.json 2>/dev/null; then
  echo "ERROR: API not ready"
  if [ -f "$API_LOG" ]; then
    tail -200 "$API_LOG"
  fi
  exit 1
fi

echo "==> Run 50-message production flow"
uv run python scripts/dev/production_flow_50.py \
  --base-url "$BASE_URL" \
  --output-dir reports/production-flow \
  --timeout 120 \
  --fail-on-findings \
  --continue-on-error

echo "==> Latest result"
LATEST_JSON="$(ls -t reports/production-flow/production_flow_50_*.json | head -1)"
uv run python - <<PY
import json
from pathlib import Path

path = Path("$LATEST_JSON")
report = json.loads(path.read_text())
print("latest:", path)
print(json.dumps(report["summary"], indent=2, sort_keys=True))
PY

echo "production flow test completed"
