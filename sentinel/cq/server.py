"""Tiny job wrapper so n8n can trigger a CloudQuery sync over HTTP and get a completion record back.
CloudQuery only reads LocalStack and writes inventory_raw; it has no Custodian code and no action credentials."""
import hmac
import os
import subprocess
import threading
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException

TOKEN = os.environ["CQ_RUNNER_TOKEN"]
app = FastAPI(title="cloudquery-job")
lock = threading.Lock()


@app.get("/health")
def health():
    return {"status": "ok", "aws_plugin": os.environ.get("CQ_AWS_VERSION"), "pg_plugin": os.environ.get("CQ_PG_VERSION")}


@app.post("/sync")
def sync(authorization: str = Header("")):
    if not hmac.compare_digest(authorization, f"Bearer {TOKEN}"):
        raise HTTPException(401)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "a sync is already running")
    try:
        started = datetime.now(timezone.utc)
        p = subprocess.run(["/usr/local/bin/cloudquery", "sync", "/spec/spec.yml", "--log-console", "--no-log-file", "--telemetry-level", "none"],
                           capture_output=True, text=True, timeout=600)
        return {"sync_id": uuid.uuid4().hex[:12], "started_at": started.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
                "exit_code": p.returncode, "plugin_versions": {"aws": os.environ.get("CQ_AWS_VERSION"), "postgresql": os.environ.get("CQ_PG_VERSION"),
                                                               "cli": "6.45.0"},
                "log_tail": (p.stdout + p.stderr)[-2500:]}
    finally:
        lock.release()
