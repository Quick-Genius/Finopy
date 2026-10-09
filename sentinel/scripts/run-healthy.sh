#!/usr/bin/env bash
# Dataset 1 (healthy): wipe, seed, run one analysis, deliver the "all clear" digests to Slack.
exec "$(dirname "$0")/demo.sh" healthy
