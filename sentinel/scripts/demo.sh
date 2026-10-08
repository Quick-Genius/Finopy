#!/usr/bin/env bash
# Run the seeded datasets against the REAL Slack channel (SLACK_WEBHOOK_URL in .env), one after the other.
#   ./scripts/demo.sh            healthy, then problem   (default)
#   ./scripts/demo.sh healthy    only the all-clear dataset
#   ./scripts/demo.sh problem    only the dataset with issues + budget overrun
# Each profile: wipe + reseed (reset.sh), trigger one analysis, wait until every Slack event is delivered.
# Approval forms (problem profile) are NOT clicked for you: a human decides, then Slack gets APPROVED/REJECTED.
set -euo pipefail
cd "$(dirname "$0")/.."
which=${1:-both}
case "$which" in both|healthy|problem) ;; *) echo "usage: $0 [healthy|problem|both]"; exit 1;; esac

env_get() { awk -F= -v k="$1" '$1==k {sub(/^[^=]*=/,""); print; exit}' .env; }
[ -n "$(env_get SLACK_WEBHOOK_URL)" ] || { echo "SLACK_WEBHOOK_URL is empty in .env: nothing would reach Slack"; exit 1; }
psql_q() { docker compose exec -T postgres psql -U sentinel -d sentinel -At -c "$1"; }

run_profile() {
  local p=$1
  echo; echo "=================== $p ==================="
  ./scripts/reset.sh "$p" >/dev/null 2>&1
  local n8n_port; n8n_port=$(env_get N8N_HOST_PORT); n8n_port=${n8n_port:-5678}
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://localhost:${n8n_port}/webhook/sentinel-run-now" -H "x-run-now-secret: $(env_get RUN_NOW_SECRET)")
  echo "analysis triggered (HTTP $code)"
  [ "$code" = 200 ] || { echo "run-now failed"; exit 1; }
  for i in $(seq 1 90); do                       # wait until events exist and all are delivered
    left=$(psql_q "select count(*) filter (where delivered_at is null) || ',' || count(*) from outbox")
    [ "$left" = "0,0" ] || [ "${left%%,*}" != 0 ] || break
    sleep 3
  done
  [ "$p" = problem ] && sleep 25                 # let the approval forms get published and announced
  echo "events (undelivered,total): $(psql_q "select count(*) filter (where delivered_at is null) || ',' || count(*) from outbox")"
  echo "cases:"; psql_q "select '  '||tenant_id||'  '||case_id||'  '||state from cases order by tenant_id, case_id"
  psql_q "select '  budget '||tenant_id||': \$'||(policy_profile->>'monthly_budget_usd') from tenants order by 1"
  forms=$(psql_q "select '  '||tenant_id||': '||form_url from approvals where status='requested' and form_url is not null")
  [ -z "$forms" ] || { echo "approval forms waiting for a human (needs approver ID + PIN):"; echo "$forms"; }
  echo "-> check #aws-sentinel in Slack now"
}

if [ "$which" = both ]; then
  run_profile healthy
  read -r -p $'\nHealthy done. Look at Slack, then press Enter to run the PROBLEM dataset... ' _
  run_profile problem
else
  run_profile "$which"
fi
