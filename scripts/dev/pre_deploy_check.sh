#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Pre-deployment safety gate for the BookCraft AI chatbot backend.
#
# Run this after ANY code change, BEFORE restarting the pm2 process, to make
# sure nothing is broken. It fails fast and returns a non-zero exit code if any
# hard gate fails, so it is safe to chain:  ./pre_deploy_check.sh && pm2 restart ai-chatbot-backend
#
# Hard gates (block deploy): import smoke, changed-file lint, test suite.
# Soft gates (warn only):    dependency health, type check.
#
# Usage:
#   scripts/dev/pre_deploy_check.sh                 # full check
#   scripts/dev/pre_deploy_check.sh --quick         # unit tests only (skip integration)
#   scripts/dev/pre_deploy_check.sh --full          # make integration failures a hard gate
#   scripts/dev/pre_deploy_check.sh --with-restart  # also restart pm2 + hit /healthz
#   scripts/dev/pre_deploy_check.sh --strict-deps    # make dependency health a hard gate
# ---------------------------------------------------------------------------
set -uo pipefail

ROOT="/var/www/ai-chatbot-backend"
cd "$ROOT"

# shellcheck disable=SC1091
source .venv/bin/activate 2>/dev/null || { echo "FATAL: cannot activate .venv"; exit 2; }

QUICK=0; WITH_RESTART=0; STRICT_DEPS=0; FULL=0
for arg in "$@"; do
  case "$arg" in
    --quick) QUICK=1 ;;              # unit tests only, skip integration entirely
    --full) FULL=1 ;;               # make integration failures a hard gate too
    --with-restart) WITH_RESTART=1 ;;
    --strict-deps) STRICT_DEPS=1 ;;
    *) echo "unknown arg: $arg"; exit 2 ;;
  esac
done

red()   { printf "\033[31m%s\033[0m\n" "$*"; }
grn()   { printf "\033[32m%s\033[0m\n" "$*"; }
ylw()   { printf "\033[33m%s\033[0m\n" "$*"; }
hdr()   { printf "\n\033[1m== %s ==\033[0m\n" "$*"; }

FAILURES=0
WARNINGS=0
fail() { red "  ✗ $*"; FAILURES=$((FAILURES+1)); }
warn() { ylw "  ! $*"; WARNINGS=$((WARNINGS+1)); }
ok()   { grn "  ✓ $*"; }

# ---------------------------------------------------------------------------
hdr "1/5  Import smoke (does the app still import?)"
if python -c "import bookcraft.api.main" 2>/tmp/predeploy_import.err; then
  ok "bookcraft.api.main imports cleanly"
else
  fail "import failed:"; sed 's/^/      /' /tmp/predeploy_import.err
fi

# ---------------------------------------------------------------------------
hdr "2/5  Lint on changed files (no NEW lint introduced)"
mapfile -t CHANGED < <(git diff --name-only --diff-filter=ACM HEAD -- '*.py'; git ls-files --others --exclude-standard -- '*.py')
if [ "${#CHANGED[@]}" -eq 0 ]; then
  ok "no changed .py files to lint"
else
  if ruff check "${CHANGED[@]}" 2>/tmp/predeploy_ruff.out; then
    ok "ruff clean on ${#CHANGED[@]} changed file(s)"
  else
    fail "ruff errors in changed files:"; sed 's/^/      /' /tmp/predeploy_ruff.out | head -40
  fi
fi

# ---------------------------------------------------------------------------
hdr "3/5  Unit test suite (HARD gate — the real safety net)"
# Unit tests are fast (~15s) and deterministic. xfail/skip do NOT count as failures.
if python -m pytest tests/unit -q -p no:cacheprovider -o addopts="" >/tmp/predeploy_pytest.out 2>/dev/null; then
  ok "$(grep -aoE '[0-9]+ passed[0-9a-z, ]*' /tmp/predeploy_pytest.out | tail -1)"
else
  fail "unit test failures:"
  grep -aE "^FAILED|ERROR|[0-9]+ failed" /tmp/predeploy_pytest.out | grep -vE "Transient|export traces" | sed 's/^/      /' | head -40
fi

# Integration tests need live services / API keys; many are environment-gated and
# skip or fail for reasons unrelated to a code change. So they are INFORMATIONAL by
# default (compare the count to your known baseline). Use --full to make them a hard gate.
if [ "$QUICK" -eq 0 ]; then
  hdr "3b/5  Integration tests (informational — env-gated; --full to enforce)"
  python -m pytest tests/integration -q -p no:cacheprovider -o addopts="" >/tmp/predeploy_int.out 2>/dev/null
  INT_SUMMARY=$(grep -aoE "[0-9]+ (passed|failed|skipped|error)[a-z ]*" /tmp/predeploy_int.out | tr '\n' ' ')
  INT_FAILED=$(grep -aoE "[0-9]+ failed" /tmp/predeploy_int.out | head -1 | grep -oE "[0-9]+" || echo 0)
  if [ "${FULL:-0}" -eq 1 ] && [ "${INT_FAILED:-0}" -gt 0 ]; then
    fail "integration failures (--full): $INT_SUMMARY"
  elif [ "${INT_FAILED:-0}" -gt 0 ]; then
    warn "integration: $INT_SUMMARY (compare to baseline; --full to enforce)"
  else
    ok "integration: $INT_SUMMARY"
  fi
fi

# ---------------------------------------------------------------------------
hdr "4/5  Dependency health (redis/pg/es/tei)"
declare -A PORTS=( [redis]=6380 [postgres]=55432 [tei]=8080 [elasticsearch]=9200 [backend]=8001 )
for name in "${!PORTS[@]}"; do
  p="${PORTS[$name]}"
  if ss -ltn 2>/dev/null | grep -q ":$p "; then
    ok "$name up (:$p)"
  else
    if [ "$STRICT_DEPS" -eq 1 ]; then fail "$name DOWN (:$p)"; else warn "$name DOWN (:$p)"; fi
  fi
done
ES_HEALTH=$(curl -s -m 5 http://127.0.0.1:9200/_cluster/health 2>/dev/null | grep -oE '"status":"[a-z]+"' | cut -d'"' -f4)
case "$ES_HEALTH" in
  green|yellow) ok "elasticsearch cluster: $ES_HEALTH" ;;
  "") warn "elasticsearch health unreachable" ;;
  *) warn "elasticsearch cluster: $ES_HEALTH" ;;
esac

# ---------------------------------------------------------------------------
hdr "5/5  Type check (informational — repo has known mypy debt)"
if command -v mypy >/dev/null 2>&1; then
  NEW_TYPE=$(timeout 180 mypy src 2>/dev/null | tail -1 || true)
  [ -n "$NEW_TYPE" ] && ylw "  mypy: $NEW_TYPE (not a hard gate)" || ok "mypy clean"
else
  warn "mypy not installed"
fi

# ---------------------------------------------------------------------------
hdr "Summary"
echo "  failures (hard): $FAILURES   warnings (soft): $WARNINGS"
if [ "$FAILURES" -ne 0 ]; then
  red "PRE-DEPLOY CHECK FAILED — do NOT deploy."
  exit 1
fi
grn "PRE-DEPLOY CHECK PASSED."

# ---------------------------------------------------------------------------
if [ "$WITH_RESTART" -eq 1 ]; then
  hdr "Restart + smoke"
  pm2 restart ai-chatbot-backend >/dev/null 2>&1 && ok "pm2 restarted ai-chatbot-backend" || { fail "pm2 restart failed"; exit 1; }
  for i in $(seq 1 10); do
    code=$(curl -s -m 3 -o /dev/null -w "%{http_code}" http://127.0.0.1:8001/healthz 2>/dev/null)
    [ "$code" = "200" ] && { ok "/healthz 200 (after ${i}s)"; break; }
    sleep 1
    [ "$i" = "10" ] && { fail "/healthz never returned 200"; exit 1; }
  done
  grn "DEPLOY SMOKE PASSED."
fi
exit 0
