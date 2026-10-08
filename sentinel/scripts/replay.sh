#!/usr/bin/env bash
# One command: reset to the known seeded scenario, run the whole governed loop through n8n, print the pass/fail proof.
set -euo pipefail
cd "$(dirname "$0")/.."
./scripts/reset.sh
python3 scripts/e2e.py
