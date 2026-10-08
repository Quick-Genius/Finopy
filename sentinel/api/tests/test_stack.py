"""Integration tests against the RUNNING stack (seeded + analysed). Run with:
   docker compose exec -e N8N_API_TOKEN=... api pytest tests/test_stack.py
They exercise the real database (RLS, immutability) and the real HTTP API. Skipped unless STACK_TESTS=1."""
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import psycopg
import pytest

pytestmark = pytest.mark.skipif(os.environ.get("STACK_TESTS") != "1", reason="needs the running stack (STACK_TESTS=1)")
API = os.environ.get("API_URL", "http://localhost:8000")
SECRET = os.environ.get("N8N_HMAC_SECRET", "")
N8N_TOKEN = os.environ.get("N8N_API_TOKEN", "")


def tok(pid):
    return {"authorization": f"Bearer tok_{pid}_demo"}


def get(path, pid):
    return httpx.get(API + path, headers=tok(pid), timeout=20)


def cases(pid):
    return {c["case_id"]: c for c in get("/cases", pid).json()}


def signed_callback(body: dict, token=N8N_TOKEN, secret=SECRET, ts=None):
    from app.security import canonical, sign
    raw = canonical(body).encode()
    ts = str(ts or int(time.time()))
    return httpx.post(API + "/approve", content=raw, timeout=20, headers={
        "authorization": f"Bearer {token}", "x-sentinel-timestamp": ts, "x-sentinel-signature": sign(secret, ts, raw), "content-type": "application/json"})


def pending(pid):
    return next(c for c in cases(pid).values() if c["state"] == "PENDING")


def packet(pid, case_id):
    return get(f"/cases/{case_id}", pid).json()


# ---------------------------------------------------------------- tenant isolation (API)
def test_each_tenant_lists_only_its_own_cases():
    p, a = cases("payments-analyst"), cases("analytics-analyst")
    assert p and a and all(k.startswith("case_payments_") for k in p) and all(k.startswith("case_analytics_") for k in a)


def test_payments_cannot_read_or_act_on_an_analytics_case_by_guessing_its_id():
    aid = next(iter(cases("analytics-analyst")))
    assert get(f"/cases/{aid}", "payments-analyst").status_code == 404
    assert get(f"/cases/{aid}/audit", "payments-analyst").status_code == 404
    r = httpx.post(f"{API}/cases/{aid}/dry-run", headers=tok("payments-analyst"), timeout=20)
    assert r.status_code == 404
    # and the other way round
    pid = next(iter(cases("payments-analyst")))
    assert get(f"/cases/{pid}", "analytics-analyst").status_code == 404


def test_audit_export_is_scoped_to_the_callers_tenant():
    rows = [json.loads(l) for l in get("/audit/export", "payments-analyst").text.splitlines() if l]
    assert rows and {r["tenant_id"] for r in rows} == {"payments"}


def test_client_supplied_tenant_id_is_not_authorization():
    r = httpx.get(API + "/cases", params={"tenant_id": "analytics"}, headers=tok("payments-analyst"), timeout=20)
    assert all(c["case_id"].startswith("case_payments_") for c in r.json())
    r = httpx.post(API + "/analyze", json={"tenant_id": "analytics"}, headers=tok("payments-analyst"), timeout=20)
    assert r.status_code == 422                       # extra field forbidden; tenant is never read from the body


def test_unauthenticated_and_wrong_role_are_rejected():
    assert httpx.get(API + "/cases", timeout=20).status_code == 401
    assert httpx.get(API + "/cases", headers={"authorization": "Bearer nope"}, timeout=20).status_code == 401
    assert httpx.post(API + "/approve", json={}, headers=tok("payments-approver"), timeout=20).status_code == 403   # humans cannot call the callback


# ---------------------------------------------------------------- callback gate
def cb(pk, **over):
    a, c = pk["approval"], pk["case_id"]
    body = {"approval_id": a["approval_id"], "case_id": c, "decision": "approve", "actor_id": "payments-approver", "actor_proof": "1111",
            "case_version": pk["version"], "policy_sha256": a["policy_sha256"], "target_set_sha256": a["target_set_sha256"],
            "expires_at": a["expires_at"], "idempotency_key": "t-" + uuid.uuid4().hex}
    body.update(over)
    return body


def analytics_pending_packet():
    return packet("analytics-analyst", pending("analytics-analyst")["case_id"])


@pytest.mark.skipif(not N8N_TOKEN, reason="needs N8N_API_TOKEN")
class TestCallbackRefusals:
    def test_bad_signature_and_stale_timestamp(self):
        pk = analytics_pending_packet()
        body = cb(pk, actor_id="analytics-approver", actor_proof="3333")
        assert signed_callback(body, secret="wrong").status_code == 401
        assert signed_callback(body, ts=int(time.time()) - 1000).status_code == 401

    def test_payments_approver_cannot_decide_an_analytics_approval(self):
        pk = analytics_pending_packet()
        r = signed_callback(cb(pk))                              # actor payments-approver, valid PIN, valid signature
        assert r.status_code == 403 and "tenant" in r.json()["error"]
        assert packet("analytics-analyst", pk["case_id"])["state"] == "PENDING"

    def test_wrong_pin_wrong_hash_wrong_version_wrong_expiry(self):
        pk = analytics_pending_packet()
        good = dict(actor_id="analytics-approver", actor_proof="3333")
        assert signed_callback(cb(pk, **{**good, "actor_proof": "0000"})).status_code == 403
        assert signed_callback(cb(pk, **good, policy_sha256="0" * 64)).status_code == 409
        assert signed_callback(cb(pk, **good, target_set_sha256="0" * 64)).status_code == 409
        assert signed_callback(cb(pk, **good, case_version=99)).status_code == 409
        assert signed_callback(cb(pk, **good, expires_at=(datetime.now(timezone.utc) + timedelta(days=9)).isoformat())).status_code == 409
        assert signed_callback(cb(pk, decision="timeout", actor_id="system:n8n-timeout", actor_proof=None)).status_code == 409   # not yet expired
        assert packet("analytics-analyst", pk["case_id"])["state"] == "PENDING"

    def test_analyst_role_cannot_approve(self):
        pk = analytics_pending_packet()
        assert signed_callback(cb(pk, actor_id="analytics-analyst", actor_proof="x")).status_code == 403


# ---------------------------------------------------------------- database-level guarantees
def conn(tenant=None, system=False):
    c = psycopg.connect(os.environ["DATABASE_URL"], autocommit=False)
    if tenant:
        c.execute("select set_config('app.tenant_id', %s, true)", (tenant,))
    if system:
        c.execute("select set_config('app.system', 'on', true)")
    return c


def test_rls_fails_closed_and_isolates():
    with conn() as c:
        assert c.execute("select count(*) from cases").fetchone()[0] == 0                       # no tenant bound -> nothing
    with conn("payments") as c:
        assert {r[0] for r in c.execute("select distinct tenant_id from cases")} == {"payments"}
        assert c.execute("select count(*) from cost_facts where tenant_id='analytics'").fetchone()[0] == 0
    with conn("analytics") as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("insert into audit_events (tenant_id, event_type, actor) values ('payments','x','y')")   # cannot write across tenants


def test_append_only_tables_and_no_delete_grant():
    # the app role has no UPDATE/DELETE privilege on append-only tables at all (privilege error, stronger than the trigger)
    for stmt in ("update case_versions set content='{}'", "delete from audit_events", "update audit_events set actor='tamper'",
                 "update evidence set payload='{}'", "update policies set policy_yaml='x'", "delete from cases", "truncate audit_events"):
        with conn("payments") as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(stmt)


def test_append_only_triggers_stop_even_the_owner():
    owner = os.environ.get("OWNER_DATABASE_URL")
    if not owner:
        pytest.skip("owner URL deliberately not present in the API container")
    with psycopg.connect(owner) as c, pytest.raises(psycopg.errors.RaiseException):
        c.execute("update audit_events set actor='tamper'")
