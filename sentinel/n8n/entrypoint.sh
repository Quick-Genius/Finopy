#!/bin/sh
# Import the three workflows and activate them, then run n8n. Imported fresh on every container start.
set -e
n8n import:workflow --separate --input=/workflows
n8n update:workflow --all --active=true
exec n8n start
