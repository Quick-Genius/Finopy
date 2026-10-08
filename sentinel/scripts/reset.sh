#!/usr/bin/env bash
# Reset to the known seeded scenario: wipes DB, n8n state, runner markers and LocalStack (it is ephemeral).
# Keeps the CloudQuery plugin cache so the next start does not re-download plugins.
set -euo pipefail
cd "$(dirname "$0")/.."
# usage: ./scripts/reset.sh [healthy|problem]   (default: the profile in .env, else problem)
if [ -n "${1:-}" ]; then
  case "$1" in healthy|problem) ;; *) echo "profile must be healthy or problem"; exit 1;; esac
  touch .env; grep -v '^SEED_PROFILE=' .env > .env.tmp || true; echo "SEED_PROFILE=$1" >> .env.tmp; mv .env.tmp .env; chmod 600 .env
fi
docker compose down --remove-orphans
for v in pgdata n8n_data runner_state; do docker volume rm -f "finops-sentinel_$v" >/dev/null; done
./scripts/up.sh
