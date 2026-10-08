"""Dashboard read model: one tenant-scoped call that returns everything the overview page draws.

Money is returned as strings with two decimals (never floats). The tenant is always the caller's.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends

from . import config
from .auth import HUMAN, Principal
from .db import tx
from .security import utcnow

router = APIRouter(tags=["overview"])

NEEDS_ACTION = ("INVESTIGATED", "PENDING")
IN_FLIGHT = ("PENDING", "APPROVED", "EXECUTED", "VERIFIED")
SPEND_DAYS = 45


def _m(d) -> str:
    return f"{Decimal(d or 0):.2f}"


def _top(totals: dict, counts: dict, key: str, n: int) -> list:
    rows = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
    return [{key: k, "monthly": _m(v), "cases": counts[k]} for k, v in rows]


def build(cases: list, spend: list, approvals: list, activity: list, anchor) -> dict:
    """Pure transformer over query rows, so it is unit-testable without a database."""
    by_state, by_workload, by_owner = defaultdict(int), defaultdict(Decimal), defaultdict(Decimal)
    wl_n, ow_n = defaultdict(int), defaultdict(int)
    in_flight = identified = Decimal(0)
    for c in cases:
        by_state[c["state"]] += 1
        identified += c["monthly"]
        if c["state"] in IN_FLIGHT:
            in_flight += c["monthly"]
        owner = c["owner"] or "unassigned"
        by_workload[c["workload"]] += c["monthly"]; wl_n[c["workload"]] += 1
        by_owner[owner] += c["monthly"]; ow_n[owner] += 1

    daily = {r["usage_date"]: Decimal(r["amount"]) for r in spend}
    last7 = sum((v for d, v in daily.items() if d > anchor - timedelta(days=7)), Decimal(0))
    prev7 = sum((v for d, v in daily.items() if anchor - timedelta(days=14) < d <= anchor - timedelta(days=7)), Decimal(0))
    change = None if prev7 == 0 else round((last7 - prev7) / prev7 * 100, 1)

    wl = {c["case_id"]: c["workload"] for c in cases}
    return {
        "currency": "USD",
        "totals": {"cases": len(cases), "needs_action": sum(by_state.get(s, 0) for s in NEEDS_ACTION), "pending_approvals": by_state.get("PENDING", 0),
                   "verified": by_state.get("VERIFIED", 0), "refused": by_state.get("REFUSED", 0), "identified_monthly": _m(identified),
                   "in_flight_monthly": _m(in_flight), "realized": {"status": "not_measured", "amount": None}},
        "by_state": {k: v for k, v in by_state.items() if v},
        "savings_by_workload": _top(by_workload, wl_n, "workload", 6),
        "savings_by_owner": _top(by_owner, ow_n, "owner", 5),
        "spend": {"points": [{"date": d.isoformat(), "amount": _m(v)} for d, v in sorted(daily.items())],
                  "last_7d": _m(last7), "previous_7d": _m(prev7), "change_pct": float(change) if change is not None else None,
                  "window_start": (anchor - timedelta(days=config.WINDOW_DAYS)).isoformat(), "window_end": anchor.isoformat()},
        "pending_approvals": [{"approval_id": a["approval_id"], "case_id": a["case_id"], "workload": wl.get(a["case_id"]), "required_role": a["required_role"],
                               "expires_at": a["expires_at"], "projected_monthly": _m(next((c["monthly"] for c in cases if c["case_id"] == a["case_id"]), 0))}
                              for a in approvals],
        "recent_activity": [{"audit_id": e["audit_id"], "event_type": e["event_type"], "actor": e["actor"], "case_id": e["case_id"],
                             "workload": wl.get(e["case_id"]), "to_state": e["to_state"], "created_at": e["created_at"]} for e in activity],
    }


@router.get("/overview")
def overview(p: Principal = Depends(HUMAN)):
    t = p.tenant_id
    with tx(t) as c:
        cases = c.execute("""select cs.case_id, cs.state, v.content->>'workload' as workload, v.content->'attribution'->>'owner' as owner,
                                    coalesce((v.content->'recommendation'->'projected_savings'->>'monthly')::numeric, 0) as monthly
                             from cases cs join case_versions v on v.case_id=cs.case_id and v.version=cs.current_version
                             where cs.tenant_id=%s""", (t,)).fetchall()
        anchor = c.execute("select max(usage_date) as d from cost_facts where tenant_id=%s", (t,)).fetchone()["d"] or utcnow().date()
        spend = c.execute("select usage_date, sum(amount) as amount from cost_facts where tenant_id=%s and usage_date > %s and usage_date <= %s "
                          "group by usage_date order by usage_date", (t, anchor - timedelta(days=SPEND_DAYS), anchor)).fetchall()
        approvals = c.execute("select approval_id, case_id, required_role, expires_at from approvals where tenant_id=%s and status='requested' "
                              "order by expires_at", (t,)).fetchall()
        activity = c.execute("select audit_id, event_type, actor, case_id, to_state, created_at from audit_events where tenant_id=%s "
                             "order by audit_id desc limit 10", (t,)).fetchall()
    return build(cases, spend, approvals, activity, anchor)
