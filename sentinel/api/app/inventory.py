"""Turn CloudQuery's raw tables (schema inventory_raw) into tenant-scoped, immutable inventory snapshots.

CloudQuery stays tenant-agnostic. Here each resource is assigned to a tenant only by its validated TenantId tag;
a resource with a missing or unknown TenantId is counted as `unscoped` and never stored (fail closed).
Agent fields are never written into the raw tables.
"""
from datetime import datetime, timezone

from .db import J, tx
from .security import utcnow


def _tags(v) -> dict:
    if not v:
        return {}
    if isinstance(v, dict):
        return {str(k): str(x) for k, x in v.items()}
    return {t["Key"]: t["Value"] for t in v if "Key" in t}


def _dt(v):
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return None


def read_raw() -> list[dict]:
    out = []
    with tx(system=True) as c:
        for r in c.execute("select * from inventory_raw.aws_ec2_instances").fetchall():
            tags = _tags(r.get("tags"))
            out.append({"resource_id": r["instance_id"], "resource_type": "ec2_instance", "region": r.get("region") or "us-east-1",
                        "account_id": r.get("account_id") or "", "state": (r.get("state") or {}).get("Name", "unknown"),
                        "tags": tags, "launch_time": _dt(r.get("launch_time")), "attached": None,
                        "name": tags.get("Name"), "vpc_id": r.get("vpc_id")})
        for r in c.execute("select * from inventory_raw.aws_ec2_ebs_volumes").fetchall():
            tags = _tags(r.get("tags"))
            out.append({"resource_id": r["volume_id"], "resource_type": "ebs_volume", "region": r.get("region") or "us-east-1",
                        "account_id": r.get("account_id") or "", "state": r.get("state") or "unknown",
                        "tags": tags, "launch_time": _dt(r.get("create_time")), "attached": bool(r.get("attachments")),
                        "name": tags.get("Name"), "vpc_id": None})
    return out


def ingest(sync: dict) -> dict[str, str]:
    """Create one snapshot per tenant from the current raw tables. Returns {tenant_id: snapshot_id}."""
    raw = read_raw()
    with tx(system=True) as c:
        tenants = {r["tenant_id"] for r in c.execute("select tenant_id from tenants").fetchall()}
    by_tenant: dict[str, list[dict]] = {t: [] for t in tenants}
    unscoped = 0
    for r in raw:
        t = r["tags"].get("TenantId")
        if t in tenants:
            by_tenant[t].append(r)
        else:
            unscoped += 1
    out = {}
    for t, rows in by_tenant.items():
        sid = f"snap_{t}_{sync['sync_id']}"
        with tx(t) as c:
            n = c.execute(
                """insert into inventory_snapshots (snapshot_id, tenant_id, sync_id, source, status, plugin_versions,
                       sync_started_at, sync_finished_at, resource_count, unscoped_count)
                   values (%s,%s,%s,%s,'complete',%s,%s,%s,%s,%s) on conflict (snapshot_id) do nothing""",
                (sid, t, sync["sync_id"], "cloudquery:aws@localstack", J(sync.get("plugin_versions", {})),
                 sync.get("started_at"), sync.get("finished_at") or utcnow(), len(rows), unscoped)).rowcount
            if n:
                for r in rows:
                    c.execute(
                        """insert into inventory_resources (snapshot_id, tenant_id, resource_id, resource_type, region,
                               account_id, state, tags, launch_time, attached, name, vpc_id)
                           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (sid, t, r["resource_id"], r["resource_type"], r["region"], r["account_id"], r["state"],
                         J(r["tags"]), r["launch_time"], r["attached"], r["name"], r["vpc_id"]))
        out[t] = sid
    return out
