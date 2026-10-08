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
docker compose up -d --build
echo -n "waiting for services"
for i in $(seq 1 120); do
  ok=1
  curl -sf localhost:8000/health >/dev/null 2>&1 || ok=0
  curl -sf localhost:5678/healthz >/dev/null 2>&1 || ok=0
  curl -sf localhost:3000/ >/dev/null 2>&1 || ok=0
  # n8n is healthy before its webhooks register; an unsigned POST answers 401 once the intake workflow is live
  [ "$(curl -s -o /dev/null -w '%{http_code}' -X POST localhost:5678/webhook/sentinel-case 2>/dev/null)" = 401 ] || ok=0
  [ "$ok" = 1 ] && break
  echo -n "."; sleep 3
done
echo
curl -s localhost:8000/health; echo
# first-run n8n owner so the editor UI is usable (webhooks/forms work without it)
curl -s -o /dev/null -X POST localhost:5678/rest/owner/setup -H 'content-type: application/json' \
  -d '{"email":"demo@sentinel.local","firstName":"Demo","lastName":"Owner","password":"Sentinel-Demo-1"}' || true
echo "dashboard http://localhost:3000   api http://localhost:8000/docs   n8n http://localhost:5678 (demo@sentinel.local / Sentinel-Demo-1)"
