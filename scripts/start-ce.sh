#!/usr/bin/env bash
# Start the SuperTale CE API and bundled frontend locally.
set -euo pipefail

root_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root_dir"

api_port="${NOVELVIDEO_API_PORT:-8780}"
# Loopback by default. The `novelvideo api` command couples FileAuthPort's
# exposure decision to the address actually passed to Uvicorn (see
# src/novelvideo/cli.py), so binding 0.0.0.0 without ST_LOCAL_API_TOKEN makes
# every CE session probe return 401 and the SPA bounces to /login forever.
# NOVELVIDEO_PUBLIC_HOST cannot override that from here. Bind a LAN address only
# on purpose, together with ST_LOCAL_API_TOKEN.
api_host="${NOVELVIDEO_API_HOST:-127.0.0.1}"
frontend_port="${SUPERTALE_FE_PORT:-5173}"
frontend_host="${SUPERTALE_FE_HOST:-0.0.0.0}"
api_ready_timeout="${NOVELVIDEO_API_READY_TIMEOUT:-90}"
api_pid=""
fe_pid=""

cleanup() {
  trap - INT TERM EXIT
  if [ -n "$fe_pid" ] && kill -0 "$fe_pid" >/dev/null 2>&1; then
    echo "Stopping frontend..."
    kill "$fe_pid" >/dev/null 2>&1 || true
  fi
  if [ -n "$api_pid" ] && kill -0 "$api_pid" >/dev/null 2>&1; then
    echo "Stopping API..."
    kill "$api_pid" >/dev/null 2>&1 || true
  fi
  if [ -n "$fe_pid" ] || [ -n "$api_pid" ]; then
    wait "$fe_pid" "$api_pid" 2>/dev/null || true
  fi
}

trap cleanup INT TERM EXIT

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it first: https://docs.astral.sh/uv/" >&2
  exit 2
fi

if ! command -v pnpm >/dev/null 2>&1; then
  echo "pnpm is required for the bundled frontend. Install it first: corepack enable && corepack prepare pnpm@11.5.0 --activate" >&2
  exit 2
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required for the local API health check." >&2
  exit 2
fi

if [ ! -f ".env" ]; then
  if [ -f ".env.example" ]; then
    cp .env.example .env
    echo "Created .env from .env.example."
    echo "Edit .env and set NEWAPI_BASE_URL / NEWAPI_API_KEY for generation features."
  else
    echo ".env.example is missing; continuing with shell environment only." >&2
  fi
fi

if [ -f ".env" ]; then
  set -a
  # shellcheck source=/dev/null
  source ".env"
  set +a
fi

# Force standalone CE mode. Empty strings intentionally override any .env values.
export ST_EDITION=ce
export ST_CONTROL_PLANE_DSN=
export ST_REDIS_URL=
export ST_CELERY_BROKER_URL=
export ST_CELERY_RESULT_BACKEND=
# The browser-facing origin is loopback whenever the API binds loopback (the
# default above). `novelvideo api` derives NOVELVIDEO_PUBLIC_HOST from --host
# itself, so exporting it here would be overwritten and must not be relied on.
# CE is normally served over plain HTTP in local development; allow the
# HttpOnly cookie to work there if a caller explicitly uses the login route.
export ST_COOKIE_SECURE="${ST_COOKIE_SECURE:-0}"
export NOVELVIDEO_API_HOST="$api_host"
export NOVELVIDEO_API_PORT="$api_port"
export NOVELVIDEO_API_URL="http://127.0.0.1:${api_port}"
export DRAMACLAW_API_URL="$NOVELVIDEO_API_URL"
export SUPERTALE_API_URL="$NOVELVIDEO_API_URL"
# Keep the browser pointed at the loopback API and make the CE fallback
# explicit when the first runtime-config request is interrupted during boot.
export VITE_API_URL="$NOVELVIDEO_API_URL"
export VITE_EDITION=ce

if [ "${NEWAPI_API_KEY:-}" = "your_newapi_token" ] || [ -z "${NEWAPI_API_KEY:-}" ]; then
  echo "Warning: NEWAPI_API_KEY is not configured. API can start, but AI generation will fail." >&2
fi

if [ -z "${ST_LOCAL_API_TOKEN:-}" ]; then
  case "$api_host" in
    127.*|localhost|::1|"[::1]") ;;
    *)
      echo "Warning: NOVELVIDEO_API_HOST=$api_host is not a loopback address and ST_LOCAL_API_TOKEN is empty." >&2
      echo "         Every session probe will return 401 and the browser will loop on /login." >&2
      echo "         Set ST_LOCAL_API_TOKEN before exposing the API beyond this machine." >&2
      ;;
  esac
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Warning: ffmpeg is not on PATH. Video/audio processing may fail." >&2
fi

if [ ! -d ".venv" ]; then
  echo "Installing dependencies with uv sync --group dev ..."
  uv sync --group dev
fi

if [ ! -d "frontend/node_modules" ]; then
  echo "Installing frontend dependencies with pnpm install ..."
  (cd frontend && pnpm install)
fi

echo "Starting SuperTale CE API at http://${api_host}:${api_port}/api/v1"
echo "Health check: http://127.0.0.1:${api_port}/api/v1/config"
uv run novelvideo api --host "$api_host" --port "$api_port" &
api_pid="$!"

echo "Waiting for API readiness..."
api_ready_url="http://127.0.0.1:${api_port}/api/v1/config"
api_ready_deadline=$((SECONDS + api_ready_timeout))
until curl -fsS --max-time 2 "$api_ready_url" >/dev/null 2>&1; do
  if ! kill -0 "$api_pid" >/dev/null 2>&1; then
    echo "API process exited before becoming ready." >&2
    exit 1
  fi
  if [ "$SECONDS" -ge "$api_ready_deadline" ]; then
    echo "API did not become ready within ${api_ready_timeout}s: ${api_ready_url}" >&2
    exit 1
  fi
  sleep 1
done
echo "API is ready."

# /api/v1/config answers even when every session probe is rejected, so readiness
# alone is not enough: verify that the SPA can actually establish a CE session.
# Without this check a misconfigured bind only surfaces as an endless /login
# redirect loop in the browser.
if [ -z "${ST_LOCAL_API_TOKEN:-}" ]; then
  auth_probe_url="http://127.0.0.1:${api_port}/api/v1/auth/me"
  auth_status="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$auth_probe_url" 2>/dev/null || true)"
  if [ "$auth_status" = "401" ]; then
    echo "Startup check failed: GET ${auth_probe_url} returned 401 while CE advertises auth_required=false." >&2
    echo "The frontend would loop on /login. The API is bound to '${api_host}' without ST_LOCAL_API_TOKEN." >&2
    echo "Fix: unset NOVELVIDEO_API_HOST to use the loopback default, or set ST_LOCAL_API_TOKEN." >&2
    exit 1
  fi
fi

echo "Starting SuperTale CE frontend at http://127.0.0.1:${frontend_port}"
echo "Frontend API target comes from frontend/.env"
(
  cd frontend
  pnpm dev --host "$frontend_host" --port "$frontend_port"
) &
fe_pid="$!"

echo "Press Ctrl+C to stop."

while true; do
  if ! kill -0 "$api_pid" >/dev/null 2>&1; then
    echo "API process exited."
    exit 1
  fi
  if ! kill -0 "$fe_pid" >/dev/null 2>&1; then
    echo "Frontend process exited."
    exit 1
  fi
  sleep 1
done
