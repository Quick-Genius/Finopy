#!/usr/bin/env bash
# One-command start: creates .env with random secrets on first run, builds, starts, waits for health.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -f .env ]; then
  cp .env.example .env
  for k in POSTGRES_PASSWORD APP_DB_PASSWORD CQ_DB_PASSWORD N8N_DB_PASSWORD N8N_ENCRYPTION_KEY N8N_HMAC_SECRET RUNNER_HMAC_SECRET N8N_API_TOKEN CQ_RUNNER_TOKEN RUN_NOW_SECRET; do
    v=$(openssl rand -hex 16)
    # portable in-place edit (BSD + GNU sed)
    sed -i.bak "s|^$k=.*|$k=$v|" .env && rm -f .env.bak
  done
  echo "created .env with generated secrets"
fi

# Read only the numeric host-port settings from .env; never source secrets as shell code.
env_port() {
  local key="$1" value
  value=$(awk -F= -v key="$key" '$1 == key { print $2; exit }' .env)
  value=${value:-$2}
  if [[ ! "$value" =~ ^[0-9]{1,5}$ ]] || [ "$value" -lt 1 ] || [ "$value" -gt 65535 ]; then
    echo "invalid $key in .env (expected a TCP port)" >&2
    exit 1
  fi
  printf '%s' "$value"
}
POSTGRES_HOST_PORT=$(env_port POSTGRES_HOST_PORT 5433)
LOCALSTACK_HOST_PORT=$(env_port LOCALSTACK_HOST_PORT 4566)
API_HOST_PORT=$(env_port API_HOST_PORT 8000)
N8N_HOST_PORT=$(env_port N8N_HOST_PORT 5678)
DASHBOARD_HOST_PORT=$(env_port DASHBOARD_HOST_PORT 3000)
export POSTGRES_HOST_PORT LOCALSTACK_HOST_PORT API_HOST_PORT N8N_HOST_PORT DASHBOARD_HOST_PORT

docker compose up -d --build
echo -n "waiting for services"
for i in $(seq 1 120); do
  ok=1
  curl -sf "http://localhost:${API_HOST_PORT}/health" >/dev/null 2>&1 || ok=0
  curl -sf "http://localhost:${N8N_HOST_PORT}/healthz" >/dev/null 2>&1 || ok=0
  curl -sf "http://localhost:${DASHBOARD_HOST_PORT}/" >/dev/null 2>&1 || ok=0
  # n8n is healthy before its webhooks register; an unsigned POST answers 401 once the intake workflow is live
  [ "$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://localhost:${N8N_HOST_PORT}/webhook/sentinel-case" 2>/dev/null)" = 401 ] || ok=0
  [ "$ok" = 1 ] && break
  echo -n "."; sleep 3
done
echo
curl -s "http://localhost:${API_HOST_PORT}/health"; echo
# first-run n8n owner so the editor UI is usable (webhooks/forms work without it)
curl -s -o /dev/null -X POST "http://localhost:${N8N_HOST_PORT}/rest/owner/setup" -H 'content-type: application/json' \
  -d '{"email":"demo@sentinel.local","firstName":"Demo","lastName":"Owner","password":"Sentinel-Demo-1"}' || true
echo "dashboard http://localhost:${DASHBOARD_HOST_PORT}   api http://localhost:${API_HOST_PORT}/docs   n8n http://localhost:${N8N_HOST_PORT} (demo@sentinel.local / Sentinel-Demo-1)"
