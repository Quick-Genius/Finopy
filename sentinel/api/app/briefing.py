"""Human-readable investigation briefing: Issue / Has to be fixed / Recommendation / Stats / Who acts.

Deterministic. Every number and every piece of advice is computed here from stored facts; the LLM neither sees nor writes any of it.
This system investigates and alerts: it never fixes anything by itself. The only action it can ever take, after a human approves,
is a reversible tag. Recommendations are advice for people.
"""
from datetime import timedelta
from decimal import Decimal

from . import config
from .db import tx
from .security import utcnow

BLOCKS = "▁▂▃▄▅▆▇█"


def esc(s) -> str:
    return str(s if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def usd(x) -> str:
    return f"${float(x):,.2f}"


def spark(vals: list[float], lo: float | None = None, hi: float | None = None) -> str:
    """Unicode sparkline. Pass lo/hi for a FIXED scale (so tiny noise is not stretched into big swings)."""
    if not vals:
        return ""
    lo = min(vals) if lo is None else lo
    hi = max(vals) if hi is None else hi
    if hi == lo:
        return BLOCKS[3] * len(vals)
    return "".join(BLOCKS[max(0, min(7, int((v - lo) / (hi - lo) * 7 + 0.5)))] for v in vals)


def bar(v: float, vmax: float, width: int = 14) -> str:
    n = 0 if vmax <= 0 else max(1 if v > 0 else 0, round(v / vmax * width))
    return "█" * n + "░" * (width - n)


def short(rid: str) -> str:
    return "…" + rid[-6:]


def load_stats(tenant_id: str, p: dict) -> dict:
    end = utcnow().date()
    with tx(tenant_id) as c:
        rows = c.execute("select usage_date, sum(amount) as a from cost_facts where tenant_id=%s and workload=%s and service=%s "
                         "and usage_date >= %s and usage_date < %s group by usage_date order by usage_date",
                         (tenant_id, p["workload"], p["service"], end - timedelta(days=14), end)).fetchall()
        cpu = {}
        for rid in p["candidates"][:3]:
            cpu[rid] = [float(r["cpu_avg"]) for r in c.execute(
                "select cpu_avg from utilization_facts where tenant_id=%s and resource_id=%s and usage_date >= %s and usage_date < %s order by usage_date",
                (tenant_id, rid, end - timedelta(days=7), end)).fetchall()]
    return {"daily": [float(r["a"]) for r in rows], "cpu": cpu}


def stats_block(p: dict, st: dict) -> str:
    L, w = [], 16
    d = st["daily"]
    if len(d) >= 3:
        L.append("cost/day (14d)".ljust(w) + spark(d) + f"  {usd(d[0])} -> {usd(d[-1])}")
        L.append(" " * w + " " * (len(d) - 3) + "^^^ analysis window")
    sp = p["spend"]
    if sp["status"] == "anomaly":
        a, e = float(sp["actual"]), float(sp["expected"])
        L.append("3 days expected".ljust(w) + bar(e, max(a, e)) + f" {usd(e)}")
        L.append("3 days actual".ljust(w) + bar(a, max(a, e)) + f" {usd(a)}")
    for rid, vals in st["cpu"].items():
        if vals:
            L.append(f"CPU {short(rid)}".ljust(w) + spark(vals, 0, 10) + f"  avg {sum(vals) / len(vals):.1f}% (scale 0-10%, idle < 5%)")
    return "\n".join(L)


def build(p: dict, st: dict) -> dict:
    sp, own, n = p["spend"], p["owner"], len(p["candidates"])
    monthly = Decimal(p.get("candidate_monthly") or "0")
    protected = p["risk"] == "high" or p["state"] == "REFUSED"
    issue, fix, rec = [], [], []
    tag_mode = config.ACTION_MODE == "tag_after_approval"

    if sp["status"] == "anomaly":
        a, e = Decimal(sp["actual"]), Decimal(sp["expected"])
        pct = f" (+{(a - e) / e * 100:.0f}%)" if e > 0 else ""
        issue.append(f"{esc(p['service'])} spend for *{esc(p['workload'])}* was {usd(a)} over the last {sp['window_days']} days vs {usd(e)} expected{pct}.")
    cpus = [sum(v) / len(v) for v in st["cpu"].values() if v]
    if p["kind"] == "idle-ec2" and cpus:
        rng = f"{min(cpus):.1f}%" if max(cpus) - min(cpus) < 0.05 else f"{min(cpus):.1f}%-{max(cpus):.1f}%"
        issue.append(f"{n} instance(s) look idle: average CPU {rng} over 7 days (idle threshold 5%).")
    elif p["waste_indicators"]:
        issue += [esc(w) for w in p["waste_indicators"][:2]]
    if own["status"] != "attributed":
        issue.append(f"Owner could not be established: {esc(own['reason'])}.")

    if p["classification"] == "legitimate":
        fix.append("Nothing: a planned event explains this spend.")
        rec.append("No action. Re-check once the event ends.")
    elif p["classification"] == "needs_review" or own["status"] != "attributed":
        fix.append("Ownership records disagree, so nobody can safely act yet.")
        rec += ["Owner and platform team compare the Owner tag with the deployment record and correct whichever is wrong.",
                "Then re-run the analysis. Until ownership is clear, no approval is possible."]
    elif p["kind"] == "unattached-ebs":
        fix.append(f"An unused storage volume costs about {usd(monthly)}/month.")
        rec += [f"Owner {esc(own['name'])} confirms the volume is unused.",
                "If unused: snapshot it first, then delete it by hand. The system never deletes anything" + ("; it only adds a review tag." if tag_mode else " and changes nothing.")]
    elif protected:
        fix.append("Idle-looking resource(s) are protected production: they must not be changed automatically.")
        rec += ["Platform team reviews whether the standby is right-sized (for example a smaller instance type) in a planned change window.",
                "Do not stop it: it is tagged Protected / production."]
    else:
        fix.append(f"{n} idle instance(s) cost about {usd(monthly)}/month while doing almost no work.")
        rec.append(f"Owner {esc(own['name'])} confirms the instances are not needed.")
        if tag_mode:
            rec += [f"If yes, a human approves the reversible tag (mark for stop in 2 days). Rollback: {esc(p['rollback'])}",
                    "The owner then stops them by hand. Realized savings are not measured here."]
        else:
            rec += ["If yes, an approver records the decision (for the audit trail) and the owner stops them by hand. The system itself changes nothing.",
                    "Realized savings are not measured here."]

    ap, st_ = p["approval"] or {}, p["state"]
    if st_ == "PENDING":
        human = ("An approver opens the form and approves or rejects (ID + PIN). Nothing changes until a human decides."
                 if tag_mode else "An approver opens the form and records approve or reject (ID + PIN). Either way the system changes nothing: the owner fixes it by hand.")
    elif st_ == "INVESTIGATED" and (p["classification"] == "needs_review" or own["status"] != "attributed"):
        human = "Owner / platform team must resolve ownership. No approval is possible yet."
    elif st_ == "REFUSED":
        human = "No automatic action is allowed on this resource. For human review only."
    elif st_ == "APPROVED" and not tag_mode:
        human = f"Approved by {esc(ap.get('decided_by'))}. The owner now fixes it by hand; the system changed nothing."
    elif st_ in ("APPROVED", "EXECUTED"):
        human = f"Approved by {esc(ap.get('decided_by'))}; the reversible tag is being applied."
    elif st_ == "VERIFIED":
        human = f"Approved by {esc(ap.get('decided_by'))}; tag applied and confirmed in the live resource state."
    elif st_ in ("REJECTED", "EXPIRED"):
        human = "No change was made. Re-open the case if the waste is still a concern."
    else:
        human = "Human review."
    b = {"issue": issue, "fix": fix, "recommendation": rec, "stats": stats_block(p, st), "who_acts": human}
    b["text"] = render(b)
    return b


def render(b: dict) -> str:
    out = ["*Issue*"] + [f"• {x}" for x in b["issue"] or ["No material issue found."]]
    out += ["", "*Has to be fixed*"] + [f"• {x}" for x in b["fix"]]
    out += ["", "*Recommendation* _(advice for humans; the system fixes nothing by itself)_"] + [f"• {x}" for x in b["recommendation"]]
    if b["stats"]:
        out += ["", "*Stats*", "```", b["stats"], "```"]
    out += ["", f"*Who acts:* {b['who_acts']}"]
    return "\n".join(out)
