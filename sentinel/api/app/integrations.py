"""Integration catalog with live status. Status is only ever what the API can observe; anything it cannot see says so.

Planned connectors are not faked: a workspace can register interest (audited), nothing more.
"""
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict

from . import config, runner_client
from .auth import HUMAN, Principal
from .db import tx
from .security import utcnow
from .states import audit

log = logging.getLogger("sentinel.integrations")
router = APIRouter(tags=["integrations"])
Provider = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{1,30}$")]

# availability: "live" connectors are built and probed; "planned" ones only accept a request for interest.
CATALOG = [
    {"key": "aws", "name": "Amazon Web Services", "category": "cloud", "availability": "live",
     "description": "CloudQuery inventory and Cloud Custodian dry-runs for AWS accounts."},
    {"key": "azure", "name": "Microsoft Azure", "category": "cloud", "availability": "planned", "description": "Cost and inventory for Azure subscriptions."},
    {"key": "gcp", "name": "Google Cloud", "category": "cloud", "availability": "planned", "description": "Billing export and inventory for GCP projects."},
    {"key": "slack", "name": "Slack", "category": "notifications", "availability": "live",
     "description": "One-way channel alerts for approvals and outcomes, delivered by an n8n workflow."},
    {"key": "jira", "name": "Jira", "category": "notifications", "availability": "planned", "description": "Open a ticket for every case that needs a human owner."},
    {"key": "teams", "name": "Microsoft Teams", "category": "notifications", "availability": "planned", "description": "Approval and outcome alerts in a Teams channel."},
    {"key": "pagerduty", "name": "PagerDuty", "category": "notifications", "availability": "planned", "description": "Page the owner when a cost anomaly is severe."},
    {"key": "n8n", "name": "n8n workflows", "category": "platform", "availability": "live",
     "description": "Case intake, the approval form and scheduled analysis. Events reach n8n through a signed outbox."},
    {"key": "runner", "name": "Cloud Custodian runner", "category": "platform", "availability": "live",
     "description": "The only service holding action credentials. Runs dry-runs and, only if enabled, approved actions."},
    {"key": "database", "name": "PostgreSQL", "category": "platform", "availability": "live",
     "description": "Cases, evidence, approvals and the audit log, isolated per tenant by row-level security."},
    {"key": "ai", "name": "AI summaries", "category": "platform", "availability": "live",
     "description": "Investigation notes written from verified evidence and fact-checked before they are shown."},
]
PLANNED = {i["key"] for i in CATALOG if i["availability"] == "planned"}
KEYS = {i["key"] for i in CATALOG}


def _s(state: str, detail: str, **facts) -> dict:
    return {"state": state, "detail": detail, "facts": [{"label": k.replace("_", " "), "value": v} for k, v in facts.items() if v is not None]}


def _age_minutes(ts) -> int | None:
    return None if ts is None else max(int((utcnow() - ts).total_seconds() // 60), 0)


def _ago(ts) -> str:
    m = _age_minutes(ts)
    return "never" if m is None else ("just now" if m < 1 else f"{m} min ago" if m < 120 else f"{m // 60} h ago")


def probe_aws(c, t):
    snap = c.execute("select sync_finished_at, created_at, resource_count from inventory_snapshots where tenant_id=%s and status='complete' "
                     "order by created_at desc limit 1", (t,)).fetchone()
    env = "Demo (LocalStack, dummy credentials)" if config.DEMO_MODE else "Live"
    if not snap:
        return _s("not_configured", "No inventory snapshot yet. Run the scheduled analysis workflow in n8n.", environment=env)
    synced = snap["sync_finished_at"] or snap["created_at"]
    fresh = _age_minutes(synced) * 60 <= config.SNAPSHOT_MAX_AGE_SECONDS
    return _s("connected" if fresh else "degraded",
              "Inventory is fresh enough to analyse." if fresh else f"Inventory is older than the {config.SNAPSHOT_MAX_AGE_SECONDS // 60} min freshness limit; analysis will refuse to act on it.",
              environment=env, resources=snap["resource_count"], last_sync=_ago(synced))


# Database probes run inside the caller's transaction and are deliberately not guarded: a failed statement poisons the
# transaction, so a database error should surface as an error rather than as a half-true page.
def read_outbox(c, t):
    return c.execute("""select count(*) filter (where delivered_at is null) as pending,
                               count(*) filter (where delivered_at is null and attempts >= 3) as stuck,
                               max(delivered_at) as last_ok,
                               max(delivered_at) filter (where event_type='case.state_changed') as last_notify
                        from outbox where tenant_id=%s""", (t,)).fetchone()


def probe_n8n(ob):
    if ob["stuck"]:
        return _s("degraded", f"{ob['stuck']} event(s) failed delivery at least 3 times and are still retrying.", queued=ob["pending"], last_delivery=_ago(ob["last_ok"]))
    return _s("connected", "Events are being delivered." if ob["last_ok"] else "No events delivered yet.", queued=ob["pending"], last_delivery=_ago(ob["last_ok"]))


def probe_slack(ob):
    return _s("managed_externally", "The Slack webhook lives in n8n, so the API cannot verify it. Set it up with scripts/slack_connect.py.",
              last_alert_handed_to_n8n=_ago(ob["last_notify"]))


def probe_database(c):
    r = c.execute("select rolsuper, rolbypassrls from pg_roles where rolname=current_user").fetchone()
    safe = r is not None and not r["rolsuper"] and not r["rolbypassrls"]
    return _s("connected" if safe else "degraded", "Row-level security applies to the API role." if safe else "The API role can bypass row-level security.",
              row_level_security="enforced" if safe else "bypassable")


def probe_runner():
    try:
        ok = runner_client.health().get("status") == "ok"
    except Exception as e:      # the runner is a separate service; being down is a status, not an API error
        log.warning("runner probe failed: %s", type(e).__name__)
        ok = False
    return _s("connected" if ok else "down", "Reachable." if ok else "The runner did not answer its health check.", action_mode=config.ACTION_MODE)


def probe_ai():
    if config.LLM_PROVIDER and config.LLM_API_KEY:
        return _s("connected", "Summaries are phrased by the model, then fact-checked against the evidence.", provider=config.LLM_PROVIDER, model=config.LLM_MODEL or None)
    return _s("not_configured", "Using plain template summaries. Set LLM_PROVIDER and LLM_API_KEY to enable AI phrasing.")


@router.get("/integrations")
def list_integrations(p: Principal = Depends(HUMAN)):
    t = p.tenant_id
    with tx(t) as c:
        interest = {r["provider"] for r in c.execute("select provider from integration_interest where tenant_id=%s and active", (t,)).fetchall()}
        ob = read_outbox(c, t)
        probes = {"aws": probe_aws(c, t), "n8n": probe_n8n(ob), "slack": probe_slack(ob), "database": probe_database(c)}
    probes["runner"], probes["ai"] = probe_runner(), probe_ai()
    out = []
    for i in CATALOG:
        st = _s("planned", "Not built yet. Register interest to help us prioritise it.") if i["availability"] == "planned" else probes[i["key"]]
        out.append({**i, **st, "interested": i["key"] in interest})
    live = [i for i in out if i["availability"] == "live"]
    return {"generated_at": utcnow(), "integrations": out,
            "summary": {"connected": sum(i["state"] == "connected" for i in live), "needs_attention": sum(i["state"] in ("degraded", "down") for i in live),
                        "externally_managed": sum(i["state"] == "managed_externally" for i in live), "planned": len(out) - len(live)}}


class InterestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interested: bool


@router.put("/integrations/{provider}/interest")
def set_interest(provider: Provider, body: InterestIn, p: Principal = Depends(HUMAN)):
    if provider not in PLANNED:
        if provider not in KEYS:
            raise HTTPException(404, "unknown integration")
        raise HTTPException(409, "this integration is already available; interest applies to planned ones")
    with tx(p.tenant_id) as c:
        c.execute("""insert into integration_interest (tenant_id, provider, active, requested_by) values (%s,%s,%s,%s)
                     on conflict (tenant_id, provider) do update set active=excluded.active, requested_by=excluded.requested_by, updated_at=now()""",
                  (p.tenant_id, provider, body.interested, p.principal_id))
        audit(c, p.tenant_id, None, "integration.interest", p.principal_id, detail={"provider": provider, "interested": body.interested})
    return {"provider": provider, "interested": body.interested}
