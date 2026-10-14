#!/usr/bin/env bash
# End-to-end tests in demo mode: start a fresh API (SQLite, DEMO_MODE) and the built dashboard, run e2e/, stop both.
#
#   scripts/e2e.sh                     # needs: backend deps + Playwright's Chromium, and `npm run build` in frontend/
#   PYTHON=backend/.venv/bin/python scripts/e2e.sh
# The dashboard's /api proxy target is fixed when it's built: with another API_PORT, build it with
# BACKEND_URL=http://localhost:$API_PORT first.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-python}"
case "$PYTHON" in /*) ;; */*) PYTHON="$PWD/$PYTHON" ;; esac  # a relative path still works after the cd below
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-3000}"
TMP="$(mktemp -d)"
LOGS="${E2E_LOGS:-$TMP}"

export ENVIRONMENT=development DEMO_MODE=true REDIS_URL= CELERY_TASK_ALWAYS_EAGER=false
export DATABASE_URL="sqlite:///$TMP/e2e.db" LOCAL_STORAGE_PATH="$TMP/storage"
export SECRET_KEY="e2e-secret-key-that-is-long-enough-0123456789"
export PUBLIC_API_URL="http://localhost:$API_PORT" FRONTEND_URL="http://localhost:$WEB_PORT"
export CORS_ORIGINS="http://localhost:$WEB_PORT,http://127.0.0.1:$WEB_PORT"
export ANTHROPIC_API_KEY= OPENAI_API_KEY= OLLAMA_MODEL= HUMAN_EMULATION=false NEXT_TELEMETRY_DISABLED=1

cleanup() {
  [ -n "${API_PID:-}" ] && kill "$API_PID" 2>/dev/null || true
  [ -n "${WEB_PID:-}" ] && kill "$WEB_PID" 2>/dev/null || true
}
trap cleanup EXIT

(cd "$ROOT/backend" && exec "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT") > "$LOGS/api.log" 2>&1 &
API_PID=$!
(cd "$ROOT/frontend" && BACKEND_URL="http://localhost:$API_PORT" exec node_modules/.bin/next start -p "$WEB_PORT") > "$LOGS/web.log" 2>&1 &
WEB_PID=$!

for _ in $(seq 1 90); do
  if curl -sf "http://localhost:$API_PORT/health" >/dev/null && curl -sf "http://localhost:$WEB_PORT/api/health" >/dev/null; then
    break
  fi
  sleep 1
done
curl -sf "http://localhost:$WEB_PORT/api/health" >/dev/null || { echo "servers didn't start"; tail -n 40 "$LOGS/api.log" "$LOGS/web.log"; exit 1; }

status=0
E2E_BASE_URL="http://localhost:$WEB_PORT" E2E_API_URL="http://localhost:$API_PORT" "$PYTHON" -m pytest "$ROOT/e2e" -q -s -p no:cacheprovider --rootdir "$ROOT/e2e" || status=$?
if [ "$status" -ne 0 ]; then
  echo "---- API log ----"; tail -n 60 "$LOGS/api.log"
  echo "---- dashboard log ----"; tail -n 20 "$LOGS/web.log"
fi
exit "$status"
