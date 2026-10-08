"""Isolated Cloud Custodian runner. The only component holding (dummy) action credentials.

It trusts nothing it is sent beyond the HMAC: it re-checks the policy against its own allow-list, recomputes both
digests, enforces expiry and its own hard target cap, re-reads live resources, re-runs a live dry-run, and only then
runs the real (non-destructive) policy. Each execution_id can run at most once.
"""
import hashlib
import hmac
import json
import os
import pathlib
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from importlib.metadata import version

import boto3
import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Request

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
SECRET = os.environ["RUNNER_HMAC_SECRET"]
HARD_CAP = 5
MARKER_DIR = pathlib.Path(os.environ.get("RUNNER_STATE_DIR", "/data"))
ALLOWED = {
    "aws.ec2": {"id_key": "InstanceId", "type": "ec2_instance", "states": {"running"}},
    "aws.ebs": {"id_key": "VolumeId", "type": "ebs_volume", "states": {"available"}},
}
app = FastAPI(title="custodian-runner")


def _auth(raw: bytes, ts: str, sig: str):
    try:
        fresh = abs(time.time() - int(ts)) <= 300
    except (TypeError, ValueError):
        fresh = False
    good = hmac.new(SECRET.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    if not (fresh and hmac.compare_digest(good, sig or "")):
        raise HTTPException(401, "bad signature")


async def signed(request: Request, x_sentinel_timestamp: str = Header(""), x_sentinel_signature: str = Header("")) -> dict:
    raw = await request.body()
    _auth(raw, x_sentinel_timestamp, x_sentinel_signature)
    return json.loads(raw)


def ec2():
    return boto3.client("ec2", region_name=REGION)


def check_allow_list(text: str, ids: list[str] | None = None) -> list[str]:
    """Independent of the API: refuse anything that is not exactly one of our two non-destructive templates."""
    errs = []
    try:
        doc = yaml.safe_load(text)
        pols = doc["policies"]
        assert isinstance(pols, list) and len(pols) == 1 and set(doc) == {"policies"}
        p = pols[0]
        assert set(p) == {"name", "resource", "filters", "actions"}, "unexpected policy keys"
        res = p["resource"]
        assert res in ALLOWED, f"resource {res} not allowed"
        acts = p["actions"]
        assert len(acts) == 1
        a = acts[0]
        if res == "aws.ec2":
            assert set(a) == {"type", "tag", "op", "days"} and a["type"] == "mark-for-op" and a["op"] == "stop" \
                and a["tag"] == "finops_mark" and isinstance(a["days"], int) and 1 <= a["days"] <= 7, "action not allowed"
        else:
            assert a == {"type": "tag", "key": "finops_review", "value": "unattached-ebs-v1"}, "action not allowed"
        for f in p["filters"]:
            if isinstance(f, dict) and f.get("type") in ("value", "metrics"):
                continue
            assert isinstance(f, dict) and len(f) == 1 and next(iter(f)).startswith(("tag:", "State")), f"filter not allowed: {f}"
        scope = [f for f in p["filters"] if isinstance(f, dict) and f.get("type") == "value" and f.get("key") == ALLOWED[res]["id_key"]]
        assert len(scope) == 1 and scope[0].get("op") == "in", "missing explicit id scope"
        if ids is not None:
            assert set(ids) <= set(scope[0]["value"]), "targets not inside policy scope"
    except (AssertionError, KeyError, TypeError, yaml.YAMLError) as e:
        errs.append(f"allow-list: {e}")
    return errs


def run_c7n(text: str, name: str, dry: bool) -> dict:
    with tempfile.TemporaryDirectory() as d:
        pf, out = pathlib.Path(d, "policy.yml"), pathlib.Path(d, "out")
        pf.write_text(text)
        cmd = ["custodian", "run", "-s", str(out), "--region", REGION] + (["--dryrun"] if dry else []) + [str(pf)]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        rj = out / name / "resources.json"
        resources = json.loads(rj.read_text()) if rj.exists() else []
        return {"exit_code": p.returncode, "log": f"$ {' '.join(cmd[:2])} {'--dryrun ' if dry else ''}...\n{p.stdout}\n{p.stderr}"[-6000:], "resources": resources}


def _norm(r: dict, id_key: str) -> dict:
    tags = {t["Key"]: t["Value"] for t in r.get("Tags", [])}
    st = r.get("State")
    return {"id": r[id_key], "tags": tags, "state": st["Name"] if isinstance(st, dict) else st}


@app.get("/health")
def health():
    return {"status": "ok", "custodian": version("c7n"), "region": REGION, "endpoint": os.environ.get("AWS_ENDPOINT_URL")}


@app.post("/validate")
def validate(body: dict = Depends(signed)):
    errs = check_allow_list(body["policy_yaml"])
    if errs:
        return {"ok": False, "log": "\n".join(errs), "version": version("c7n")}
    with tempfile.TemporaryDirectory() as d:
        pf = pathlib.Path(d, "policy.yml")
        pf.write_text(body["policy_yaml"])
        p = subprocess.run(["custodian", "validate", str(pf)], capture_output=True, text=True, timeout=60)
    return {"ok": p.returncode == 0, "exit_code": p.returncode, "log": (p.stdout + p.stderr)[-3000:], "version": version("c7n")}


@app.post("/dryrun")
def dryrun(body: dict = Depends(signed)):
    errs = check_allow_list(body["policy_yaml"])
    if errs:
        raise HTTPException(422, "; ".join(errs))
    res = yaml.safe_load(body["policy_yaml"])["policies"][0]["resource"]
    r = run_c7n(body["policy_yaml"], body["policy_name"], dry=True)
    norm = {x["id"]: x for x in (_norm(y, ALLOWED[res]["id_key"]) for y in r["resources"])}
    return {"exit_code": r["exit_code"], "log": r["log"], "matched_ids": sorted(norm), "resources": norm}


@app.post("/inspect")
def inspect(body: dict = Depends(signed)):
    ids = body["ids"]
    if body["resource_type"] == "ec2_instance":
        rs = [i for rv in ec2().describe_instances(InstanceIds=ids)["Reservations"] for i in rv["Instances"]]
        return {"resources": {x["id"]: x for x in (_norm(r, "InstanceId") for r in rs)}}
    rs = ec2().describe_volumes(VolumeIds=ids)["Volumes"]
    return {"resources": {x["id"]: x for x in (_norm(r, "VolumeId") for r in rs)}}


@app.post("/execute")
def execute(g: dict = Depends(signed)):
    started = datetime.now(timezone.utc)
    ids, reasons = sorted(g["target_ids"]), []

    def out(status, **kw):
        return {"status": status, "reasons": reasons, "started_at": started.isoformat(), "ended_at": datetime.now(timezone.utc).isoformat(),
                "policy_name": g["policy_name"], "custodian": version("c7n"), "exit_code": kw.pop("exit_code", None),
                "matched_ids": kw.pop("matched_ids", []), "log": kw.pop("log", "\n".join(reasons)), **kw}

    reasons += check_allow_list(g["policy_yaml"], ids)
    if hashlib.sha256(g["policy_yaml"].encode()).hexdigest() != g["policy_sha256"]:
        reasons.append("policy digest mismatch")
    if hashlib.sha256(json.dumps(ids, sort_keys=True, separators=(",", ":")).encode()).hexdigest() != g["target_set_sha256"]:
        reasons.append("target-set digest mismatch")
    if datetime.fromisoformat(g["expires_at"]) <= started:
        reasons.append("approval expired")
    if not ids or len(ids) > min(HARD_CAP, g["max_targets"]):
        reasons.append(f"target count {len(ids)} outside 1..{min(HARD_CAP, g['max_targets'])}")
    if reasons:
        return out("refused")

    pol = yaml.safe_load(g["policy_yaml"])["policies"][0]
    spec = ALLOWED[pol["resource"]]
    try:
        live = inspect({"resource_type": spec["type"], "ids": ids})["resources"]
    except Exception as e:
        reasons.append(f"live re-read failed: {type(e).__name__}")
        return out("refused")
    for i in ids:
        r = live.get(i)
        if not r:
            reasons.append(f"{i}: not found in live state")
            continue
        if r["tags"].get("TenantId") != g["tenant_id"]:
            reasons.append(f"{i}: live TenantId {r['tags'].get('TenantId')!r} != {g['tenant_id']!r}")
        if r["tags"].get("Protected") or r["tags"].get("Environment", "").lower() == "production":
            reasons.append(f"{i}: protected/production resource")
        if r["state"] not in spec["states"]:
            reasons.append(f"{i}: live state {r['state']!r} no longer eligible")
    if reasons:
        return out("refused")
    before = {i: live[i]["tags"] for i in ids}

    dry = run_c7n(g["policy_yaml"], g["policy_name"], dry=True)
    dm = sorted(_norm(x, spec["id_key"])["id"] for x in dry["resources"])
    if dry["exit_code"] != 0 or dm != ids:
        reasons.append(f"live dry-run matched {dm}, approved {ids}")
        return out("refused", log=dry["log"], matched_ids=dm, exit_code=dry["exit_code"], before=before)

    MARKER_DIR.mkdir(parents=True, exist_ok=True)
    marker = MARKER_DIR / f"{g['execution_id']}.started"
    try:
        marker.touch(exist_ok=False)      # at-most-once, even if the API misbehaves
    except FileExistsError:
        reasons.append("execution_id already started on this runner")
        return out("refused")

    real = run_c7n(g["policy_yaml"], g["policy_name"], dry=False)
    matched = sorted(_norm(x, spec["id_key"])["id"] for x in real["resources"])
    ok = real["exit_code"] == 0 and matched == ids
    if not ok:
        reasons.append(f"custodian exit {real['exit_code']}, matched {matched}")
    return out("succeeded" if ok else "failed", exit_code=real["exit_code"], matched_ids=matched, before=before,
               log=f"--- dry-run ---\n{dry['log']}\n--- run ---\n{real['log']}")
