"""FinOps Sentinel API. Tenant and role are derived from the bearer token; see auth.py."""
import json
import logging
import threading
from contextlib import asynccontextmanager
from datetime import date

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict

from . import analysis, approval, config, execution, inventory, outbox, policy, runner_client
from .auth import Principal, current_principal, require_roles, tenant_for_case
from .db import open_pool, tx
from .security import utcnow
from .states import audit

logging.basicConfig(level=logging.INFO)


def sweep_expired() -> None:
    """Expiry is enforced by the API itself, so it holds even if n8n is down."""
    with tx(system=True) as c:
        due = c.execute("select approval_id, case_id, tenant_id from approvals where status='requested' and expires_at <= now()").fetchall()
    for d in due:
        try:
            with tx(d["tenant_id"]) as c:
                a = c.execute("select * from approvals where approval_id=%s and status='requested' for update", (d["approval_id"],)).fetchone()
                case = c.execute("select * from cases where case_id=%s for update", (d["case_id"],)).fetchone()
                if a and case["state"] == "PENDING":
                    approval._close(c, a, case, d["tenant_id"], "expired", "EXPIRED", "system:sweeper", "system", "expired without a decision")
                    audit(c, d["tenant_id"], d["case_id"], "approval.escalated", "system:sweeper", version=case["current_version"],
                          detail={"approval_id": d["approval_id"], "to": "platform reviewer (dashboard queue)", "auto_approved": False})
        except Exception:
            logging.exception("sweep failed")


def _sweeper():
    import time
    while True:
        try:
            sweep_expired()
        except Exception:
            logging.exception("sweeper error")
        time.sleep(10)


@asynccontextmanager
async def lifespan(_):
    open_pool()
    for fn in (outbox.loop, execution.loop, _sweeper):
        threading.Thread(target=fn, daemon=True).start()
    yield


app = FastAPI(title="FinOps Sentinel API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"], allow_methods=["GET", "POST"],
                   allow_headers=["authorization", "content-type"])

ANY_ROLE = require_roles("analyst", "approver", "reviewer", "finance", "automation")
HUMAN = require_roles("analyst", "approver", "reviewer", "finance")


def _case_or_404(c, tenant, case_id):
    row = c.execute("select * from cases where case_id=%s and tenant_id=%s", (case_id, tenant)).fetchone()
    if not row:
        raise HTTPException(404, "case not found")
    return row


@app.get("/health")
def health():
    out = {"api": "ok", "time": utcnow().isoformat()}
    try:
        with tx(system=True) as c:
            c.execute("select 1")
        out["database"] = "ok"
    except Exception as e:
        out["database"] = f"error: {type(e).__name__}"
    try:
        out["runner"] = runner_client.health().get("status", "unknown")
    except Exception as e:
        out["runner"] = f"error: {type(e).__name__}"
    code = 200 if out["database"] == "ok" else 503
    return JSONResponse(out, status_code=code)


@app.get("/demo/personas")
def personas():
    """Local-demo convenience only (DEMO_MODE=true): lists the seeded principals and their dummy bearer tokens."""
    if not config.DEMO_MODE:
        raise HTTPException(404)
    with tx(system=True) as c:
        rows = c.execute("select principal_id, tenant_id, role, display_name from principals where role <> 'automation' order by tenant_id, role").fetchall()
    return [{**r, "token": f"tok_{r['principal_id']}_demo"} for r in rows]


@app.get("/me")
def me(p: Principal = Depends(ANY_ROLE)):
    return {"principal_id": p.principal_id, "tenant_id": p.tenant_id, "role": p.role, "display_name": p.display_name, "scope": list(p.scope)}


# ------------------------------------------------------------------ reads (tenant derived from the principal)
@app.get("/cases")
def list_cases(p: Principal = Depends(HUMAN)):
    with tx(p.tenant_id) as c:
        rows = c.execute("""select cs.case_id, cs.state, cs.current_version, cs.created_at, cs.updated_at, v.content
                            from cases cs join case_versions v on v.case_id=cs.case_id and v.version=cs.current_version
                            where cs.tenant_id=%s order by cs.created_at, cs.case_id""", (p.tenant_id,)).fetchall()
    out = []
    for r in rows:
        k = r["content"]
        out.append({"case_id": r["case_id"], "state": r["state"], "version": r["current_version"], "workload": k["workload"], "kind": k["kind"],
                    "service": k["service"], "classification": k["assessment"]["classification"], "owner": k["attribution"]["owner"],
                    "attribution_confidence": k["attribution"]["confidence"], "risk": k["recommendation"]["risk"],
                    "priority": k["recommendation"]["priority"], "projected_monthly": k["recommendation"]["projected_savings"]["monthly"],
                    "actual": k["signal"]["actual"], "expected": k["signal"]["expected"], "created_at": r["created_at"], "updated_at": r["updated_at"]})
    return out


@app.get("/cases/{case_id}")
def get_case(case_id: str, p: Principal = Depends(ANY_ROLE)):
    t = tenant_for_case(p, case_id)
    with tx(t) as c:
        case = _case_or_404(c, t, case_id)
        v = c.execute("select * from case_versions where case_id=%s order by version", (case_id,)).fetchall()
        cur = next(x for x in v if x["version"] == case["current_version"])
        ev = c.execute("select evidence_id, type, source_ref, payload, payload_sha256 from evidence where case_id=%s and case_version=%s order by evidence_id",
                       (case_id, case["current_version"])).fetchall()
        pol = c.execute("select * from policies where case_id=%s order by created_at desc limit 1", (case_id,)).fetchone()
        apr = c.execute("select * from approvals where case_id=%s order by requested_at desc limit 1", (case_id,)).fetchone()
        exe = c.execute("select * from executions where case_id=%s order by created_at desc limit 1", (case_id,)).fetchone()
        aud = c.execute("select audit_id, event_type, actor, from_state, to_state, case_version, detail, created_at from audit_events "
                        "where case_id=%s order by audit_id", (case_id,)).fetchall()
        snap = c.execute("select snapshot_id, sync_id, sync_finished_at, plugin_versions from inventory_snapshots where snapshot_id=%s", (cur["snapshot_id"],)).fetchone()
    return {"case_id": case_id, "tenant_id": t, "state": case["state"], "version": case["current_version"], "content": cur["content"],
            "content_sha256": cur["content_sha256"], "versions": [{"version": x["version"], "created_at": x["created_at"], "content_sha256": x["content_sha256"]} for x in v],
            "evidence": ev, "snapshot": snap, "policy": pol, "approval": apr, "execution": exe, "audit": aud}


@app.get("/cases/{case_id}/audit")
def case_audit(case_id: str, p: Principal = Depends(ANY_ROLE)):
    t = tenant_for_case(p, case_id)
    with tx(t) as c:
        _case_or_404(c, t, case_id)
        return c.execute("select * from audit_events where case_id=%s order by audit_id", (case_id,)).fetchall()


@app.get("/audit/export")
def audit_export(p: Principal = Depends(require_roles("analyst", "reviewer", "finance"))):
    """Tenant-scoped NDJSON export; the tenant is the caller's, never a parameter."""
    with tx(p.tenant_id) as c:
        rows = c.execute("select * from audit_events where tenant_id=%s order by audit_id", (p.tenant_id,)).fetchall()
    body = "\n".join(json.dumps(r, default=str, sort_keys=True) for r in rows)
    return Response(body + "\n", media_type="application/x-ndjson",
                    headers={"content-disposition": f'attachment; filename="audit-{p.tenant_id}.ndjson"'})


@app.get("/series")
def series(workload: str, service: str, p: Principal = Depends(HUMAN)):
    """Real cost facts for the chart (tenant-scoped), plus the deterministic expected value per day."""
    from datetime import timedelta
    from .detect import analyze_series
    we = utcnow().date()
    with tx(p.tenant_id) as c:
        rows = c.execute("select usage_date, sum(amount) as amount from cost_facts where tenant_id=%s and workload=%s and service=%s "
                         "and usage_date >= %s group by usage_date order by usage_date", (p.tenant_id, workload, service, we - timedelta(days=45))).fetchall()
    daily = {r["usage_date"]: r["amount"] for r in rows}
    a = analyze_series(daily, we, history_days=config.HISTORY_DAYS, window_days=config.WINDOW_DAYS, min_excess=config.MIN_DAILY_EXCESS_USD)
    return {"currency": "USD", "points": [{"date": d.isoformat(), "amount": str(a_)} for d, a_ in sorted(daily.items())],
            "window_start": (we - timedelta(days=config.WINDOW_DAYS)).isoformat(), "window_end": we.isoformat(), "status": a["status"],
            "window_expected": [{"date": d["date"], "expected": d["expected"]} for d in (a.get("features") or {}).get("days", [])]}


@app.get("/summary")
def summary(p: Principal = Depends(HUMAN)):
    with tx(p.tenant_id) as c:
        rows = c.execute("""select cs.state, v.content->'recommendation'->'projected_savings'->>'monthly' as m
                            from cases cs join case_versions v on v.case_id=cs.case_id and v.version=cs.current_version where cs.tenant_id=%s""", (p.tenant_id,)).fetchall()
    from decimal import Decimal
    by_state: dict[str, int] = {}
    open_proj = Decimal(0)
    for r in rows:
        by_state[r["state"]] = by_state.get(r["state"], 0) + 1
        if r["state"] in ("PENDING", "APPROVED", "EXECUTED", "VERIFIED"):
            open_proj += Decimal(r["m"] or 0)
    return {"tenant_id": p.tenant_id, "currency": "USD", "cases_by_state": by_state, "projected_monthly_in_flight": str(open_proj),
            "realized_savings": {"status": "not_measured", "amount": None,
                                 "note": "Realized savings need billing observations over an agreed window; LocalStack cannot supply them."}}


# ------------------------------------------------------------------ analysis
class AnalyzeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str | None = None
    window_end: date | None = None


@app.post("/analyze")
def analyze(body: AnalyzeIn, p: Principal = Depends(require_roles("analyst", "reviewer"))):
    """Analyse the caller's own tenant against its newest snapshot."""
    with tx(p.tenant_id) as c:
        s = c.execute("select snapshot_id from inventory_snapshots where tenant_id=%s order by created_at desc limit 1", (p.tenant_id,)).fetchone()
    if not s:
        raise HTTPException(409, "no inventory snapshot yet: run the n8n analysis workflow (it syncs CloudQuery first)")
    key = body.idempotency_key or f"manual-{p.principal_id}-{utcnow().isoformat()}"
    return analysis.run(p.tenant_id, snapshot_id=s["snapshot_id"], idempotency_key=key, actor=p.principal_id, window_end=body.window_end)


class SyncIn(BaseModel):
    sync_id: str
    started_at: str | None = None
    finished_at: str | None = None
    plugin_versions: dict = {}
    exit_code: int = 0


class InternalAnalyzeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_ids: list[str]
    sync: SyncIn
    idempotency_prefix: str
    window_end: date | None = None


@app.post("/internal/analyze")
def internal_analyze(body: InternalAnalyzeIn, p: Principal = Depends(require_roles("automation"))):
    """n8n entry: ingest the CloudQuery snapshot once, then analyse each requested tenant inside the principal's scope."""
    if body.sync.exit_code != 0:
        raise HTTPException(409, "inventory sync failed; analysis paused (a stale snapshot may explain a case but never authorize one)")
    denied = [t for t in body.tenant_ids if t not in p.scope]
    if denied:
        raise HTTPException(403, f"tenants outside this principal's scope: {denied}")
    snaps = inventory.ingest(body.sync.model_dump())
    runs = {t: analysis.run(t, snapshot_id=snaps[t], idempotency_key=f"{body.idempotency_prefix}:{t}", actor=p.principal_id,
                            window_end=body.window_end) for t in body.tenant_ids}
    return {"snapshots": {t: snaps[t] for t in body.tenant_ids}, "runs": runs}


# ------------------------------------------------------------------ dry-run, approval
@app.post("/cases/{case_id}/dry-run")
def dry_run(case_id: str, p: Principal = Depends(require_roles("analyst", "reviewer", "automation"))):
    t = tenant_for_case(p, case_id)
    try:
        return policy.dry_run(t, case_id, p.principal_id)
    except LookupError:
        raise HTTPException(404, "case not found")
    except policy.Blocked as e:
        raise HTTPException(409, str(e))


@app.get("/approvals/{approval_id}")
def approval_packet(approval_id: str, p: Principal = Depends(ANY_ROLE)):
    """Everything an approver must see. Read from our own tables; the n8n form only displays it."""
    with tx(system=True) as c:
        row = c.execute("select tenant_id, case_id from approvals where approval_id=%s", (approval_id,)).fetchone()
    if not row or (not p.is_automation and row["tenant_id"] != p.tenant_id) or (p.is_automation and row["tenant_id"] not in p.scope):
        raise HTTPException(404, "approval not found")
    t = row["tenant_id"]
    with tx(t) as c:
        a = c.execute("select * from approvals where approval_id=%s", (approval_id,)).fetchone()
        case = c.execute("select * from cases where case_id=%s", (a["case_id"],)).fetchone()
        ver = c.execute("select * from case_versions where case_id=%s and version=%s", (a["case_id"], a["case_version"])).fetchone()
        pol = c.execute("select * from policies where policy_id=%s", (a["policy_id"],)).fetchone()
        names = {r["resource_id"]: r["name"] for r in c.execute("select resource_id, name from inventory_resources where snapshot_id=%s and tenant_id=%s",
                                                                  (ver["snapshot_id"], t)).fetchall()}
    k = ver["content"]
    return {"approval_id": approval_id, "status": a["status"], "case_id": a["case_id"], "case_state": case["state"], "case_version": a["case_version"],
            "tenant_id": t, "owner": k["attribution"]["owner"], "attribution_confidence": k["attribution"]["confidence"],
            "action": (k["recommendation"]["intent"] or {}).get("action"), "template": pol["template"], "risk": k["recommendation"]["risk"],
            "rollback": k["recommendation"]["rollback"], "why": k["assessment"]["reasons"],
            "targets": [{"id": i, "name": names.get(i)} for i in pol["target_ids"]], "target_count": len(pol["target_ids"]),
            "excluded": pol["excluded"], "projected_savings": pol["guards"]["savings"], "policy_sha256": a["policy_sha256"],
            "target_set_sha256": a["target_set_sha256"], "expires_at": a["expires_at"], "required_role": a["required_role"],
            "policy_yaml": pol["policy_yaml"]}


class FormUrlIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    form_url: str


@app.post("/approvals/{approval_id}/form-url")
def set_form_url(approval_id: str, body: FormUrlIn, p: Principal = Depends(require_roles("automation"))):
    if not body.form_url.startswith(config.N8N_FORM_BASE + "/"):
        raise HTTPException(422, "form_url must point at the configured n8n host")
    with tx(system=True) as c:
        row = c.execute("select tenant_id, case_id from approvals where approval_id=%s", (approval_id,)).fetchone()
    if not row or row["tenant_id"] not in p.scope:
        raise HTTPException(404, "approval not found")
    with tx(row["tenant_id"]) as c:
        c.execute("update approvals set form_url=%s where approval_id=%s and status='requested'", (body.form_url, approval_id))
        audit(c, row["tenant_id"], row["case_id"], "approval.form_published", p.principal_id, detail={"approval_id": approval_id})
    return {"ok": True}


@app.post("/approve")
async def approve(request: Request, p: Principal = Depends(require_roles("automation"))):
    """Signed approval callback from n8n (v2 contract). Human bearer tokens are rejected by the role dependency."""
    raw = await request.body()
    code, body = approval.handle(raw, request.headers.get("x-sentinel-timestamp", ""), request.headers.get("x-sentinel-signature", ""))
    if code == 200 and body.get("outcome") == "approved":
        execution.WAKE.set()
    return JSONResponse(body, status_code=code)
