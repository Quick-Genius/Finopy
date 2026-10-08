#!/usr/bin/env bash
# Reset to the known seeded scenario: wipes DB, n8n state, runner markers and LocalStack (it is ephemeral).
# Keeps the CloudQuery plugin cache so the next start does not re-download plugins.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose down --remove-orphans
for v in pgdata n8n_data runner_state; do docker volume rm -f "finops-sentinel_$v" >/dev/null; done
./scripts/up.sh
