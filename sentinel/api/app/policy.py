"""Constrained policy compiler + dry-run pipeline.

Policies are rendered by this code from versioned templates and a validated ActionIntent. There is no code path
that accepts policy text, shell, Terraform or SQL from a model or a client.
"""
import uuid
from datetime import timedelta

import yaml

from . import config, runner_client
from .assess import ActionIntent
from .db import J, tx
from .security import canonical, sha256_hex, target_set_hash, utcnow
from .states import StateConflict, audit, transition

CAPS = {"idle-ec2-v1": ("aws.ec2", "InstanceId", "ec2_instance"), "unattached-ebs-v1": ("aws.ebs", "VolumeId", "ebs_volume")}
TEMPLATE_VERSION = {"idle-ec2-v1": 1, "unattached-ebs-v1": 1}


def render(intent: ActionIntent, tenant_id: str, case_id: str, case_version: int) -> tuple[str, str]:
    """Return (policy_name, canonical YAML text). Tenant comes from the case row, never from the intent."""
    resource, id_key, _ = CAPS[intent.template]
    name = f"{case_id}-v{case_version}"
    scope = {"type": "value", "key": id_key, "op": "in", "value": list(intent.target_ids)}
    if intent.template == "idle-ec2-v1":
        pol = {"name": name, "resource": resource,
               "filters": [scope, {"tag:TenantId": tenant_id}, {"tag:Protected": "absent"}, {"State.Name": "running"},
                           {"type": "metrics", "name": "CPUUtilization", "namespace": "AWS/EC2", "statistics": "Average",
                            "days": config.IDLE_DAYS, "value": config.IDLE_CPU_PCT, "op": "less-than"}],
               "actions": [{"type": "mark-for-op", "tag": "finops_mark", "op": "stop", "days": intent.params.days}]}
    else:
        pol = {"name": name, "resource": resource,
               "filters": [scope, {"tag:TenantId": tenant_id}, {"tag:Protected": "absent"}, {"State": "available"},
                           {"type": "value", "key": "Attachments", "value": "empty"}],
               "actions": [{"type": "tag", "key": "finops_review", "value": "unattached-ebs-v1"}]}
    return name, yaml.safe_dump({"policies": [pol]}, sort_keys=True, default_flow_style=False)


def _is_protected(tags: dict) -> bool:
    return bool(tags.get("Protected")) or tags.get("Environment", "").lower() == "production"


class Blocked(Exception):
    """Dry-run could not run now (not an outcome that changes case state)."""


def dry_run(tenant_id: str, case_id: str, actor: str) -> dict:
    corr = "dry_" + uuid.uuid4().hex[:10]
    with tx(tenant_id) as c:
        case = c.execute("select * from cases where case_id=%s and tenant_id=%s", (case_id, tenant_id)).fetchone()
        if not case:
            raise LookupError("case not found")
        if case["state"] != "INVESTIGATED":
            return {"status": "noop", "state": case["state"], "detail": "case is not awaiting a dry-run"}
        ver = c.execute("select * from case_versions where case_id=%s and version=%s", (case_id, case["current_version"])).fetchone()
        snap = c.execute("select sync_finished_at from inventory_snapshots where snapshot_id=%s and tenant_id=%s",
                         (ver["snapshot_id"], tenant_id)).fetchone()
        inv = {r["resource_id"]: r for r in c.execute("select * from inventory_resources where snapshot_id=%s and tenant_id=%s",
                                                       (ver["snapshot_id"], tenant_id)).fetchall()}
    content = ver["content"]
    rec, attr, asmt = content["recommendation"], content["attribution"], content["assessment"]

    reasons: list[str] = []
    if rec["route"] != "dry_run" or not rec["intent"]:
        raise Blocked("case has no executable intent (route=%s)" % rec["route"])
    age = (utcnow() - snap["sync_finished_at"]).total_seconds()
    if age > config.SNAPSHOT_MAX_AGE_SECONDS:
        raise Blocked(f"inventory snapshot is stale ({int(age)}s old, limit {config.SNAPSHOT_MAX_AGE_SECONDS}s): re-sync first")
    if asmt["classification"] != "probable_waste":
        reasons.append(f"assessment is {asmt['classification']}, not probable_waste")
    if attr["status"] != "attributed" or attr["confidence"] < config.MIN_ATTRIBUTION_CONFIDENCE:
        reasons.append(f"owner not established with enough confidence ({attr['confidence']})")

    intent = ActionIntent(**rec["intent"])           # re-validated against the allow-list on every use
    cands = list(intent.target_ids)
    missing = [i for i in cands if i not in inv]
    if missing:
        reasons.append(f"candidates absent from the case snapshot: {missing}")
    protected = {i: "protected: tag Protected or Environment=production" for i in cands if i in inv and _is_protected(inv[i]["tags"])}
    name, text = render(intent, tenant_id, case_id, case["current_version"])
    p_sha = sha256_hex(text)
    runner_versions, vlog, dlog, exit_code, matched, live = {}, "", "", None, [], {}

    v = runner_client.call("/validate", {"policy_yaml": text})
    vlog, runner_versions = v["log"], {"custodian": v.get("version")}
    invalid = not v["ok"]
    if not invalid:
        d = runner_client.call("/dryrun", {"policy_yaml": text, "policy_name": name})
        dlog, exit_code, matched = d["log"], d["exit_code"], sorted(d["matched_ids"])
        live = d["resources"]
        if d["exit_code"] != 0:
            reasons.append(f"custodian dry-run exited {d['exit_code']}")

    excluded = [{"resource_id": i, "reason": protected.get(i) or "not matched by live Custodian filters"}
                for i in cands if i not in matched]
    extra = [i for i in matched if i not in cands]
    if extra:
        reasons.append(f"dry-run matched resources outside the candidate set: {extra}")
    for i in matched:
        t = (live.get(i) or {}).get("tags", {})
        if t.get("TenantId") != tenant_id:
            reasons.append(f"{i}: live TenantId tag is {t.get('TenantId')!r}, not {tenant_id!r}")
        if _is_protected(t):
            reasons.append(f"{i}: live resource is protected")
    if len(matched) > config.MAX_TARGETS:
        reasons.append(f"{len(matched)} targets exceeds the cap of {config.MAX_TARGETS}")
    if not matched and not invalid:
        reasons.append("no executable targets remain after guards" + (f" ({len(protected)} protected excluded)" if protected else ""))

    saving = {"monthly": "0.0000", "currency": "USD"}
    if matched:
        per = rec["projected_savings"]["per_resource"]
        from decimal import Decimal
        saving = {"monthly": str(sum((Decimal(per.get(i, "0")) for i in matched), Decimal(0))), "currency": "USD",
                  "assumptions": rec["projected_savings"]["assumptions"]}

    status = "invalid" if invalid else ("refused" if reasons else "valid")
    now = utcnow()
    guards = {"snapshot_age_s": int(age), "snapshot_limit_s": config.SNAPSHOT_MAX_AGE_SECONDS, "max_targets": config.MAX_TARGETS,
              "target_count": len(matched), "protected_excluded": sorted(protected), "risk": rec["risk"], "savings": saving}
    pid, aid = "pol_" + uuid.uuid4().hex[:12], "apr_" + uuid.uuid4().hex[:12]
    with tx(tenant_id) as c:
        c.execute(
            """insert into policies (policy_id, case_id, case_version, tenant_id, template, template_version, status, policy_yaml,
                   policy_sha256, candidate_ids, target_ids, target_set_sha256, excluded, refusal_reasons, guards, validate_log,
                   dryrun_log, dryrun_exit_code, runner_versions, dryrun_at)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (pid, case_id, case["current_version"], tenant_id, intent.template, TEMPLATE_VERSION[intent.template], status, text, p_sha,
             J(sorted(cands)), J(matched), target_set_hash(matched), J(excluded), J(reasons), J(guards), vlog, dlog, exit_code,
             J(runner_versions), now))
        try:
            if status == "invalid":
                transition(c, tenant_id, case_id, "INVESTIGATED", "POLICY_INVALID", actor, correlation_id=corr, detail={"policy_id": pid, "log": vlog[-500:]})
            elif status == "refused":
                transition(c, tenant_id, case_id, "INVESTIGATED", "REFUSED", actor, correlation_id=corr,
                           detail={"policy_id": pid, "reasons": reasons, "excluded": excluded})
            else:
                transition(c, tenant_id, case_id, "INVESTIGATED", "DRY_RUN", actor, correlation_id=corr,
                           detail={"policy_id": pid, "policy_sha256": p_sha, "target_set_sha256": target_set_hash(matched), "targets": matched, "excluded": excluded})
                exp = now + timedelta(seconds=config.APPROVAL_TTL_SECONDS)
                c.execute("""insert into approvals (approval_id, case_id, case_version, tenant_id, policy_id, status, required_role,
                                 policy_sha256, target_set_sha256, requested_at, expires_at)
                             values (%s,%s,%s,%s,%s,'requested','approver',%s,%s,%s,%s)""",
                          (aid, case_id, case["current_version"], tenant_id, pid, p_sha, target_set_hash(matched), now, exp))
                transition(c, tenant_id, case_id, "DRY_RUN", "PENDING", actor, correlation_id=corr,
                           detail={"approval_id": aid, "expires_at": exp.isoformat()})
                eid = "evt_" + sha256_hex(f"{aid}|approval.requested")[:20]
                c.execute("insert into outbox (event_id, tenant_id, case_id, event_type, schema_version, payload) values (%s,%s,%s,'approval.requested','1.0',%s)",
                          (eid, tenant_id, case_id, J({"event_id": eid, "event_type": "approval.requested", "schema_version": "1.0",
                                                       "tenant_id": tenant_id, "case_id": case_id, "case_version": case["current_version"],
                                                       "approval_id": aid, "policy_sha256": p_sha, "target_set_sha256": target_set_hash(matched),
                                                       "expires_at": exp.isoformat(), "required_role": "approver"})))
        except StateConflict:
            raise Blocked("case changed while the dry-run was running")
    return {"status": status, "policy_id": pid, "approval_id": aid if status == "valid" else None, "targets": matched,
            "excluded": excluded, "reasons": reasons, "policy_sha256": p_sha, "target_set_sha256": target_set_hash(matched)}
