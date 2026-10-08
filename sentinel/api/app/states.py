"""Case state machine. The API (not the UI) enforces it; each transition is compare-and-set plus an audit event."""
from .db import J
from .security import sha256_hex, utcnow

TRANSITIONS: dict[str, set[str]] = {
    "NEW": {"INVESTIGATED"},
    "INVESTIGATED": {"DRY_RUN", "REFUSED", "POLICY_INVALID", "DISMISSED"},
    "DRY_RUN": {"PENDING", "REFUSED"},
    "PENDING": {"APPROVED", "REJECTED", "EXPIRED", "INVESTIGATED"},  # INVESTIGATED = approval invalidated by a new case version
    "APPROVED": {"EXECUTED", "EXECUTION_FAILED"},
    "EXECUTED": {"VERIFIED", "VERIFICATION_FAILED"},
}
TERMINAL = {"VERIFIED", "VERIFICATION_FAILED", "REJECTED", "EXPIRED", "DISMISSED", "REFUSED", "POLICY_INVALID",
            "EXECUTION_FAILED"}


# Outcomes a human wants to hear about. PENDING is announced (with the form link) by the approval workflow itself.
NOTIFY_STATES = {"REFUSED", "POLICY_INVALID", "APPROVED", "REJECTED", "EXPIRED", "VERIFIED", "VERIFICATION_FAILED", "EXECUTION_FAILED"}


def notify_payload(tenant_id, case_id, version, from_state, to_state, actor, content, detail) -> dict:
    """Small, whitelisted, secret-free payload for Slack-style notifications."""
    d, slim = detail or {}, {}
    if isinstance(d.get("reasons"), list):
        slim["reasons"] = [str(x)[:200] for x in d["reasons"]][:5]
    elif d.get("reason"):
        slim["reasons"] = [str(d["reason"])[:200]]
    if d.get("note"):
        slim["note"] = str(d["note"])[:200]
    if isinstance(d.get("matched_ids"), list):
        slim["matched_ids"] = [str(x) for x in d["matched_ids"]][:10]
    v = d.get("verification")
    if isinstance(v, dict) and "ok" in v:
        slim["verification_ok"] = bool(v["ok"])
    k = content or {}
    rec = k.get("recommendation", {})
    import os
    return {"event_type": "case.state_changed", "schema_version": "1.0", "action_mode": os.environ.get("ACTION_MODE", "alert_only"), "tenant_id": tenant_id, "case_id": case_id, "case_version": version,
            "from_state": from_state, "to_state": to_state, "actor": actor, "detail": slim,
            "summary": {"workload": k.get("workload"), "kind": k.get("kind"), "owner": k.get("attribution", {}).get("owner"),
                        "risk": rec.get("risk"), "classification": k.get("assessment", {}).get("classification"),
                        "projected_monthly": rec.get("projected_savings", {}).get("monthly"), "currency": "USD"}}


class StateConflict(Exception):
    """The case was not in the state the caller expected (concurrent change or replay)."""


class InvalidTransition(Exception):
    pass


def audit(conn, tenant_id, case_id, event_type, actor, *, from_state=None, to_state=None, version=None,
          correlation_id=None, detail=None):
    conn.execute(
        """insert into audit_events (tenant_id, case_id, event_type, actor, from_state, to_state, case_version,
                                     correlation_id, detail) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (tenant_id, case_id, event_type, actor, from_state, to_state, version, correlation_id, J(detail or {})),
    )


def transition(conn, tenant_id, case_id, from_state, to_state, actor, *, correlation_id=None, detail=None):
    if to_state not in TRANSITIONS.get(from_state, set()):
        raise InvalidTransition(f"{from_state} -> {to_state} is not allowed")
    row = conn.execute(
        "update cases set state=%s, updated_at=now() where case_id=%s and tenant_id=%s and state=%s "
        "returning current_version",
        (to_state, case_id, tenant_id, from_state),
    ).fetchone()
    if row is None:
        raise StateConflict(f"case {case_id} is no longer {from_state}")
    audit(conn, tenant_id, case_id, f"state.{to_state.lower()}", actor, from_state=from_state, to_state=to_state,
          version=row["current_version"], correlation_id=correlation_id, detail=detail)
    if to_state in NOTIFY_STATES:
        ver = conn.execute("select content from case_versions where case_id=%s and version=%s", (case_id, row["current_version"])).fetchone()
        eid = "evt_" + sha256_hex(f"{case_id}|{from_state}|{to_state}|{utcnow().isoformat()}")[:20]
        payload = {"event_id": eid, "occurred_at": utcnow().isoformat(),
                   **notify_payload(tenant_id, case_id, row["current_version"], from_state, to_state, actor, ver["content"] if ver else None, detail)}
        conn.execute("insert into outbox (event_id, tenant_id, case_id, event_type, schema_version, payload) values (%s,%s,%s,'case.state_changed','1.0',%s) "
                     "on conflict (event_id) do nothing", (eid, tenant_id, case_id, J(payload)))
    return row["current_version"]
