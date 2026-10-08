"""Owner attribution ladder (blueprint): validated tags > dedicated account map > deployment record > name/network.

Any disagreement between evidence sources, a tenant-tag mismatch, or no evidence yields needs_review with
confidence 0. Nothing here (and no LLM) ever invents an owner: an owner must come from a reference-data row.
"""
from fnmatch import fnmatch

LADDER = {"tag": (1, 0.95), "account_map": (2, 0.90), "deployment": (3, 0.80), "naming": (4, 0.60)}


def attribute_resource(tenant: str, res: dict, ctx: dict) -> dict:
    tags = res.get("tags") or {}
    ev, notes = [], []

    t_tag, o_tag = tags.get("TenantId"), tags.get("Owner")
    if t_tag and t_tag != tenant:
        notes.append(f"tenant_tag_mismatch:{t_tag}")
    if o_tag:
        if o_tag in ctx["owners"] and t_tag == tenant:
            ev.append(("tag", o_tag, "tag:TenantId+Owner"))
        else:
            notes.append(f"invalid_owner_tag:{o_tag}")

    am = ctx["account_map"].get(res.get("account_id"))
    if am and am[0] == tenant:
        ev.append(("account_map", am[1], f"account:{res['account_id']}"))

    dep = ctx["deployments"].get(res["resource_id"])
    if dep:
        ev.append(("deployment", dep, f"deployment:{res['resource_id']}"))

    name = res.get("name") or tags.get("Name") or ""
    for pattern, owner in ctx["naming"].get("name", []):
        if name and fnmatch(name, pattern):
            ev.append(("naming", owner, f"name:{pattern}"))
    vpc_owner = ctx["naming"].get("vpc", {}).get(res.get("vpc_id"))
    if vpc_owner:
        ev.append(("naming", vpc_owner, f"vpc:{res['vpc_id']}"))

    owners = {o for _, o, _ in ev}
    items = [{"source": s, "owner": o, "ref": r, "rank": LADDER[s][0], "confidence": LADDER[s][1]} for s, o, r in ev]
    base = {"resource_id": res["resource_id"], "evidence": items, "notes": notes}
    if any(n.startswith("tenant_tag_mismatch") for n in notes):
        return {**base, "status": "needs_review", "owner": None, "confidence": 0.0, "reason": "tenant tag conflicts with scope"}
    if not owners:
        return {**base, "status": "needs_review", "owner": None, "confidence": 0.0, "reason": "no reliable ownership evidence"}
    if len(owners) > 1:
        return {**base, "status": "needs_review", "owner": None, "confidence": 0.0,
                "reason": f"conflicting owners: {sorted(owners)}"}
    return {**base, "status": "attributed", "owner": owners.pop(), "reason": "consistent evidence",
            "confidence": max(i["confidence"] for i in items)}


def attribute_group(tenant: str, resources: list[dict], ctx: dict, cost_owner_tags: set[str]) -> dict:
    """Aggregate over all candidate resources (or, with none, over cost-allocation owner tags)."""
    if not resources:
        valid = {o for o in cost_owner_tags if o in ctx["owners"]}
        if len(valid) == 1:
            o = valid.pop()
            return {"status": "attributed", "owner": o, "confidence": 0.95, "reason": "cost allocation tag",
                    "evidence": [{"source": "tag", "owner": o, "ref": "cost-allocation-tag", "rank": 1, "confidence": 0.95}],
                    "per_resource": []}
        return {"status": "needs_review", "owner": None, "confidence": 0.0, "evidence": [], "per_resource": [],
                "reason": "no resource and no valid cost allocation tag"}
    per = [attribute_resource(tenant, r, ctx) for r in resources]
    bad = [p for p in per if p["status"] != "attributed"]
    owners = {p["owner"] for p in per if p["owner"]}
    ev = [e for p in per for e in p["evidence"]]
    if bad or len(owners) != 1:
        why = bad[0]["reason"] if bad else f"candidates have different owners: {sorted(owners)}"
        return {"status": "needs_review", "owner": None, "confidence": 0.0, "reason": why, "evidence": ev, "per_resource": per}
    return {"status": "attributed", "owner": owners.pop(), "confidence": min(p["confidence"] for p in per),
            "reason": "consistent evidence", "evidence": ev, "per_resource": per}
