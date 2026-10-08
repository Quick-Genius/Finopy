"""Run one tenant analysis and persist cases + outbox events in a single transaction."""
import uuid
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from . import config
from .db import J, tx
from .graph import build_drafts
from .security import canonical, sha256_hex, utcnow
from .states import TERMINAL, audit, transition


def load(tenant: str, snapshot_id: str, window_end: date) -> dict:
    lo = window_end - timedelta(days=config.HISTORY_DAYS + config.WINDOW_DAYS + 3)
    with tx(tenant) as c:
        cost = c.execute("select usage_date, service, workload, resource_id, owner_tag, amount from cost_facts "
                         "where tenant_id=%s and usage_date >= %s and usage_date < %s", (tenant, lo, window_end)).fetchall()
        res = c.execute("select * from inventory_resources where snapshot_id=%s and tenant_id=%s", (snapshot_id, tenant)).fetchall()
        util = defaultdict(list)
        for u in c.execute("select resource_id, cpu_avg from utilization_facts where tenant_id=%s and usage_date >= %s and usage_date < %s",
                           (tenant, window_end - timedelta(days=config.IDLE_DAYS), window_end)).fetchall():
            util[u["resource_id"]].append(u["cpu_avg"])
        owners = {r["owner_id"] for r in c.execute("select owner_id from owners where tenant_id=%s", (tenant,)).fetchall()}
        am = {r["account_id"]: (r["tenant_id"], r["owner_id"]) for r in c.execute("select * from account_map where tenant_id=%s", (tenant,)).fetchall()}
        dep = {r["resource_ref"]: r["owner_id"] for r in c.execute("select * from deployments where tenant_id=%s", (tenant,)).fetchall()}
        naming = defaultdict(list)
        for r in c.execute("select * from naming_rules where tenant_id=%s", (tenant,)).fetchall():
            naming[r["kind"]].append((r["pattern"], r["owner_id"]))
        naming["vpc"] = {p: o for p, o in naming.get("vpc", [])}
        events = c.execute("select * from known_events where tenant_id=%s", (tenant,)).fetchall()
        tn = c.execute("select owner_group, policy_profile from tenants where tenant_id=%s", (tenant,)).fetchone()
    budget = (tn["policy_profile"] or {}).get("monthly_budget_usd") if tn else None
    return {"tenant": {"owner_group": tn["owner_group"] if tn else None, "monthly_budget_usd": budget}, "cost_facts": cost, "resources": res, "utilization": util,
            "ref": {"owners": owners, "account_map": am, "deployments": dep, "naming": dict(naming), "events": events}}


def _event(tenant, case_id, version, content, etype, observed_at) -> tuple[str, dict]:
    eid = "evt_" + sha256_hex(f"{case_id}|{version}|{etype}")[:20]
    rec, sig = content["recommendation"], content["signal"]
    payload = {
        "event_id": eid, "event_type": etype, "schema_version": "1.0", "case_id": case_id, "case_version": version,
        "tenant_id": tenant, "observed_at": observed_at.isoformat(),
        "signal": {"service": sig["service"], "actual": sig["actual"], "expected": sig["expected"], "currency": sig["currency"],
                   "anomaly_score": sig["anomaly_score"]},
        "attribution": {"owner": content["attribution"]["owner"], "confidence": content["attribution"]["confidence"],
                        "evidence": sorted({e["source"] for e in content["attribution"]["evidence"]})},
        "assessment": content["assessment"]["classification"],
        "recommendation": {"route": rec["route"], "action": (rec["intent"] or {}).get("action"), "template": rec["template"],
                           "monthly_saving": rec["projected_savings"]["monthly"], "currency": "USD", "risk": rec["risk"]},
        "callback": {"case_url": f"/cases/{case_id}"},
    }
    return eid, payload


def _insert_evidence(c, tenant, case_id, version, items):
    for i, (etype, ref, payload) in enumerate(items):
        c.execute("insert into evidence (evidence_id, case_id, case_version, tenant_id, type, source_ref, payload, payload_sha256) "
                  "values (%s,%s,%s,%s,%s,%s,%s,%s)",
                  (f"ev_{case_id}_v{version}_{i:02d}", case_id, version, tenant, etype, ref, J(payload), sha256_hex(canonical(payload))))


def _persist(c, tenant, draft, snapshot_id, actor, corr, now) -> str | None:
    content = draft["content"]
    sha = sha256_hex(canonical(content))
    cur = c.execute("select * from cases where tenant_id=%s and dedupe_key=%s", (tenant, draft["dedupe_key"])).fetchone()
    cid = draft["case_id"]
    if cur is None:
        c.execute("insert into cases (case_id, tenant_id, dedupe_key, state, current_version) values (%s,%s,%s,'NEW',1)",
                  (cid, tenant, draft["dedupe_key"]))
        c.execute("insert into case_versions (case_id, tenant_id, version, snapshot_id, content, content_sha256) values (%s,%s,1,%s,%s,%s)",
                  (cid, tenant, snapshot_id, J(content), sha))
        _insert_evidence(c, tenant, cid, 1, draft["evidence"])
        audit(c, tenant, cid, "case.created", actor, to_state="NEW", version=1, correlation_id=corr, detail={"content_sha256": sha})
        transition(c, tenant, cid, "NEW", "INVESTIGATED", actor, correlation_id=corr,
                   detail={"classification": content["assessment"]["classification"], "route": content["recommendation"]["route"]})
        if content["recommendation"]["route"] == "dismiss":
            transition(c, tenant, cid, "INVESTIGATED", "DISMISSED", actor, correlation_id=corr,
                       detail={"reason": "; ".join(content["assessment"]["reasons"])})
        version, etype = 1, "case.created"
    else:
        last = c.execute("select content_sha256 from case_versions where case_id=%s and version=%s", (cid, cur["current_version"])).fetchone()
        if last["content_sha256"] == sha or cur["state"] in TERMINAL:
            return None
        version = cur["current_version"] + 1
        c.execute("insert into case_versions (case_id, tenant_id, version, snapshot_id, content, content_sha256) values (%s,%s,%s,%s,%s,%s)",
                  (cid, tenant, version, snapshot_id, J(content), sha))
        _insert_evidence(c, tenant, cid, version, draft["evidence"])
        c.execute("update cases set current_version=%s, updated_at=now() where case_id=%s", (version, cid))
        audit(c, tenant, cid, "case.version_appended", actor, version=version, correlation_id=corr, detail={"content_sha256": sha})
        inv = c.execute("update approvals set status='invalidated' where case_id=%s and status='requested' returning approval_id", (cid,)).fetchall()
        for a in inv:
            audit(c, tenant, cid, "approval.invalidated", actor, version=version, correlation_id=corr,
                  detail={"approval_id": a["approval_id"], "reason": "new case version"})
        if cur["state"] == "PENDING":
            transition(c, tenant, cid, "PENDING", "INVESTIGATED", actor, correlation_id=corr, detail={"reason": "approval invalidated by new version"})
        etype = "case.updated"
    eid, payload = _event(tenant, cid, version, content, etype, now)
    c.execute("insert into outbox (event_id, tenant_id, case_id, event_type, schema_version, payload) values (%s,%s,%s,%s,'1.0',%s) "
              "on conflict (event_id) do nothing", (eid, tenant, cid, etype, J(payload)))
    return cid


def run(tenant: str, *, snapshot_id: str, idempotency_key: str, actor: str, window_end: date | None = None) -> dict:
    window_end = window_end or utcnow().date()
    window_start = window_end - timedelta(days=config.WINDOW_DAYS)
    with tx(tenant) as c:
        got = c.execute("select pg_try_advisory_xact_lock(hashtext(%s))", (f"analysis:{tenant}:{window_end}",)).fetchone()
        if not list(got.values())[0]:
            return {"status": "locked", "detail": "an analysis for this tenant/window is already running"}
        prior = c.execute("select * from analysis_runs where tenant_id=%s and (idempotency_key=%s or (window_start=%s and window_end=%s and snapshot_id=%s))",
                          (tenant, idempotency_key, window_start, window_end, snapshot_id)).fetchone()
        if prior:
            return {"status": "duplicate", "run_id": prior["run_id"], **(prior["summary"] or {})}
        snap = c.execute("select 1 from inventory_snapshots where snapshot_id=%s and tenant_id=%s", (snapshot_id, tenant)).fetchone()
        if not snap:
            return {"status": "failed", "detail": "snapshot not found for tenant"}
    data = load(tenant, snapshot_id, window_end)
    drafts = build_drafts(tenant, window_end, data)
    from . import briefing
    digest = briefing.cost_digest(tenant) if not drafts else None       # nothing to report: just say so, with the costs
    run_id, now, created = "run_" + uuid.uuid4().hex[:12], utcnow(), []
    with tx(tenant) as c:
        for d in drafts:
            if _persist(c, tenant, d, snapshot_id, f"system:analysis:{run_id}", run_id, now):
                created.append(d["case_id"])
        if digest:                                                       # ONE healthy digest per tenant per day (deterministic event id)
            eid = "evt_" + sha256_hex(f"digest|{tenant}|{window_end}")[:20]
            c.execute("insert into outbox (event_id, tenant_id, case_id, event_type, schema_version, payload) values (%s,%s,null,'digest.healthy','1.0',%s) on conflict (event_id) do nothing",
                      (eid, tenant, J({"event_id": eid, "event_type": "digest.healthy", "schema_version": "1.0", "tenant_id": tenant, "occurred_at": now.isoformat(), "text": digest})))
        summary = {"cases_changed": created, "drafts": len(drafts), "window": [window_start.isoformat(), window_end.isoformat()]}
        c.execute("insert into analysis_runs (run_id, tenant_id, idempotency_key, window_start, window_end, snapshot_id, status, finished_at, summary) "
                  "values (%s,%s,%s,%s,%s,%s,'completed',now(),%s)",
                  (run_id, tenant, idempotency_key, window_start, window_end, snapshot_id, J(summary)))
    return {"status": "completed", "run_id": run_id, **summary}
