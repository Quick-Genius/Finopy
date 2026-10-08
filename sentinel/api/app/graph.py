"""LangGraph case pipeline: ingest -> baseline -> detect -> attribute -> assess -> recommend.

Every node is deterministic code over data loaded from the tenant's own rows. There is no LLM node: money,
owners and scores are computed here; an LLM could later be added only to *explain* a finished draft or to propose
an `ActionIntent`, which `assess.ActionIntent` validates against the allow-list.
"""
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import TypedDict

from langgraph.graph import END, StateGraph

from . import config
from .assess import assess, priority, propose_intent
from .attribute import attribute_group
from .detect import analyze_series, idle_ec2, money, unattached_ebs

ROLLBACK = {
    "idle-ec2-v1": "Remove the finops_mark tag (nothing is stopped by this action); if the instance is later stopped, start it again.",
    "unattached-ebs-v1": "Remove the finops_review tag. The volume is not modified.",
}
SLUG = re.compile(r"[^a-z0-9-]+")


class S(TypedDict, total=False):
    tenant: str
    window_end: date
    data: dict
    series: dict
    spend: dict
    findings: dict
    drafts: list


def _risk(resources: list[dict]) -> str:
    envs = {(r.get("tags") or {}).get("Environment", "").lower() for r in resources}
    if any((r.get("tags") or {}).get("Protected") for r in resources) or "production" in envs:
        return "high"
    return "medium" if "staging" in envs else "low"


def n_ingest(s: S) -> S:
    d = s["data"]
    series: dict[tuple, dict[date, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    res_cost: dict[str, dict[date, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    res_workload: dict[str, str] = {}
    owner_tags: dict[str, set] = defaultdict(set)
    for f in d["cost_facts"]:
        series[(f["service"], f["workload"])][f["usage_date"]] += f["amount"]
        if f["resource_id"]:
            res_cost[f["resource_id"]][f["usage_date"]] += f["amount"]
            res_workload[f["resource_id"]] = f["workload"]
        if f["owner_tag"]:
            owner_tags[f["workload"]].add(f["owner_tag"])
    return {"series": {"daily": series, "res_cost": res_cost, "res_workload": res_workload, "owner_tags": owner_tags}}


def n_baseline(s: S) -> S:
    out = {}
    for key, daily in s["series"]["daily"].items():
        out[key] = analyze_series(dict(daily), s["window_end"], history_days=config.HISTORY_DAYS,
                                  window_days=config.WINDOW_DAYS, min_excess=config.MIN_DAILY_EXCESS_USD)
    return {"spend": out}


def n_detect(s: S) -> S:
    d, we = s["data"], s["window_end"]
    sr = s["series"]
    findings: dict[tuple, dict] = {}

    def f(workload, kind):
        return findings.setdefault((workload, kind), {"workload": workload, "kind": kind, "resources": [], "wastes": []})

    for r in d["resources"]:
        wl = sr["res_workload"].get(r["resource_id"]) or (r.get("tags") or {}).get("Workload") or "unassigned"
        if r["resource_type"] == "ec2_instance":
            res = idle_ec2(r, d["utilization"].get(r["resource_id"], []), we, idle_days=config.IDLE_DAYS,
                           cpu_pct=config.IDLE_CPU_PCT, min_points=config.IDLE_DAYS - 1, min_age_days=config.RESOURCE_MIN_AGE_DAYS)
            if res["idle"]:
                x = f(wl, "idle-ec2")
                x["resources"].append(r)
                x["wastes"].append({"resource_id": r["resource_id"], "type": "idle_ec2", "detail": res,
                                    "summary": f"{r['resource_id']} averaged {res['cpu_mean_pct']}% CPU over {res['points']} days "
                                               f"({res['days_below_threshold']} days under {res['threshold_pct']}%)"})
        else:
            res = unattached_ebs(r, we, min_days=config.RESOURCE_MIN_AGE_DAYS)
            if res["unattached"]:
                x = f(wl, "unattached-ebs")
                x["resources"].append(r)
                x["wastes"].append({"resource_id": r["resource_id"], "type": "unattached_ebs", "detail": res,
                                    "summary": f"{r['resource_id']} has been unattached for {res['age_days']} days"})
    # spend anomalies not already explained by a waste finding on the same workload+service
    waste_services = {("idle-ec2", "AmazonEC2"), ("unattached-ebs", "AmazonEBS")}
    covered = {(w, svc) for (w, k) in findings for (kk, svc) in waste_services if kk == k}
    for (service, workload), sp in s["spend"].items():
        if sp["status"] == "anomaly" and (workload, service) not in covered:
            f(workload, "spend-anomaly").update({"service": service})
    return {"findings": findings}


def n_attribute(s: S) -> S:
    d = s["data"]
    for fd in s["findings"].values():
        fd["attribution"] = attribute_group(s["tenant"], fd["resources"], d["ref"], s["series"]["owner_tags"].get(fd["workload"], set()))
    return {}


def n_assess(s: S) -> S:
    d, we = s["data"], s["window_end"]
    for (workload, kind), fd in s["findings"].items():
        service = fd.get("service") or ("AmazonEC2" if kind == "idle-ec2" else "AmazonEBS")
        fd["service"] = service
        fd["spend"] = s["spend"].get((service, workload))
        ev = [e for e in d["ref"]["events"] if e["workload"] == workload]
        fd["events"] = ev
        fd["assessment"] = assess(kind=kind, spend=fd["spend"], wastes=fd["wastes"], attribution=fd["attribution"],
                                  events=ev, window_end=we, window_days=config.WINDOW_DAYS)
    return {}


def _saving(res_cost, ids, we):
    total, per = Decimal(0), {}
    for rid in ids:
        days = [a for dte, a in res_cost.get(rid, {}).items() if we - timedelta(days=7) <= dte < we]
        monthly = money(sum(days, Decimal(0)) / len(days) * 30) if days else Decimal(0)
        per[rid] = str(monthly)
        total += monthly
    return money(total), per


def n_recommend(s: S) -> S:
    we, tenant = s["window_end"], s["tenant"]
    drafts = []
    for (workload, kind), fd in s["findings"].items():
        a, sp = fd["assessment"], fd["spend"]
        cand = sorted(r["resource_id"] for r in fd["resources"])
        risk = _risk(fd["resources"]) if fd["resources"] else "low"
        intent = None
        if a["classification"] == "probable_waste" and cand:
            intent = propose_intent(kind, cand).model_dump()
        route = "dry_run" if intent else ("dismiss" if a["classification"] == "legitimate" else "review")
        saving, per = _saving(s["series"]["res_cost"], cand, we) if cand else (Decimal(0), {})
        spend_ok = sp and sp["status"] == "anomaly"
        a_conf = sp["anomaly_score"] if spend_ok else (0.8 if fd["wastes"] else 0.0)
        prio = priority(saving, a_conf, fd["attribution"]["confidence"], risk) if cand else 0.0
        template = intent["template"] if intent else None
        content = {
            "schema_version": "1.0",
            "workload": workload, "kind": kind, "service": fd["service"],
            "signal": {
                "service": fd["service"], "currency": "USD", "status": sp["status"] if sp else "not_applicable",
                "window": {"start": (we - timedelta(days=config.WINDOW_DAYS)).isoformat(), "end": we.isoformat()},
                "actual": str(sp["actual"]) if spend_ok else None,
                "expected": str(sp["expected"]) if spend_ok else None,
                "anomaly_score": sp["anomaly_score"] if spend_ok else None,
                "features": sp["features"] if sp else None,
            },
            "waste_indicators": fd["wastes"],
            "attribution": {k: fd["attribution"][k] for k in ("status", "owner", "confidence", "reason", "evidence")},
            "assessment": a,
            "candidates": cand,
            "recommendation": {
                "route": route, "intent": intent, "template": template, "risk": risk, "priority": prio,
                "rollback": ROLLBACK.get(template, "n/a"),
                "projected_savings": {"monthly": str(saving), "currency": "USD", "per_resource": per, "status": "projected",
                                      "assumptions": ["30 x mean daily cost of the last 7 days per candidate",
                                                      "stopping removes the instance-hour charge; attached EBS keeps billing"
                                                      if kind == "idle-ec2" else
                                                      "saving materialises only if the owner later deletes the volume; this action only tags it"]},
                "realized_savings": {"status": "not_measured", "amount": None,
                                     "note": "needs post-action billing observation; LocalStack state change is not invoice evidence"},
            },
        }
        evidence = [
            ("cost_series", f"cost_facts:{tenant}/{workload}/{fd['service']}", {"service": fd["service"], "currency": "USD", "days": (sp or {}).get("features", {}).get("days", [])}),
            ("baseline", "detect.analyze_series", (sp or {}).get("features")),
            ("attribution", "attribute.attribute_group", fd["attribution"]),
            ("assessment", "assess.assess", a),
        ]
        evidence += [("waste_indicator", f"inventory:{w['resource_id']}", w) for w in fd["wastes"]]
        evidence += [("inventory", f"inventory:{r['resource_id']}", {k: (v.isoformat() if hasattr(v, 'isoformat') else v) for k, v in r.items()}) for r in fd["resources"]]
        evidence += [("known_event", f"known_events:{e['event_id']}", {k: str(v) for k, v in e.items()}) for e in fd["events"]]
        slug = SLUG.sub("-", f"{workload}-{kind}")
        drafts.append({"case_id": f"case_{tenant}_{slug}", "dedupe_key": f"{workload}:{kind}", "content": content,
                       "evidence": [e for e in evidence if e[2] is not None]})
    drafts.sort(key=lambda d: d["case_id"])
    return {"drafts": drafts}


def _build():
    g = StateGraph(S)
    for name, fn in [("ingest", n_ingest), ("baseline", n_baseline), ("detect", n_detect), ("attribute", n_attribute),
                     ("assess", n_assess), ("recommend", n_recommend)]:
        g.add_node(name, fn)
    g.set_entry_point("ingest")
    for a, b in [("ingest", "baseline"), ("baseline", "detect"), ("detect", "attribute"), ("attribute", "assess"),
                 ("assess", "recommend"), ("recommend", END)]:
        g.add_edge(a, b)
    return g.compile()


pipeline = _build()


def build_drafts(tenant: str, window_end: date, data: dict) -> list[dict]:
    return pipeline.invoke({"tenant": tenant, "window_end": window_end, "data": data})["drafts"]
