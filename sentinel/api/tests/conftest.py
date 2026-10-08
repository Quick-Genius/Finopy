import os

# unit tests import app modules that read required env at import time; give them harmless dummies
for k, v in {"DATABASE_URL": "postgresql://x:x@localhost:1/x", "N8N_HMAC_SECRET": "unit-n8n", "RUNNER_HMAC_SECRET": "unit-runner"}.items():
    os.environ.setdefault(k, v)
