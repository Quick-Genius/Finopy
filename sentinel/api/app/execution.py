"""Durable execution worker. An `executions` row exists (status=queued) before the runner is ever called.

Fail closed: any mismatch in approval, hashes, expiry, version, target count or runner re-check ends in
EXECUTION_FAILED with a recorded reason, and nothing is retried blindly.
"""
import logging
import threading
import time

from . import config, runner_client
from .db import J, tx
from .policy import CAPS
from .security import sha256_hex, target_set_hash, utcnow
from .states import audit, transition

log = logging.getLogger("sentinel.exec")
WAKE = threading.Event()


def recover_stale() -> None:
    """A 'running' row at startup means the API died mid-run: outcome unknown, so never retry automatically."""
    with tx(system=True) as c:
        for r in c.execute("update executions set status='inconclusive', ended_at=now() where status='running' returning *").fetchall():
            t = r["tenant_id"]
            audit(c, t, r["case_id"], "execution.inconclusive", "system", detail={"execution_id": r["execution_id"], "reason": "API restarted mid-run"})
            c.execute("update cases set state='EXECUTION_FAILED', updated_at=now() where case_id=%s and state='APPROVED'", (r["case_id"],))


def _claim():
    with tx(system=True) as c:
        return c.execute("""update executions set status='running', started_at=now() where execution_id =
                              (select execution_id from executions where status='queued' order by created_at limit 1 for update skip locked)
                            returning *""").fetchone()


def _finish(ex, *, status, case_state, runner=None, reasons=None, verification=None, extra_state=None):
    t = ex["tenant_id"]
    with tx(t) as c:
        c.execute("""update executions set status=%s, ended_at=now(), exit_code=%s, matched_ids=%s, result=%s, logs=%s, verification=%s
                     where execution_id=%s""",
                  (status, (runner or {}).get("exit_code"), J((runner or {}).get("matched_ids", [])),
                   J({"runner": {k: v for k, v in (runner or {}).items() if k != "log"}, "reasons": reasons or []}),
                   (runner or {}).get("log"), J(verification) if verification else None, ex["execution_id"]))
        cur = c.execute("select state from cases where case_id=%s", (ex["case_id"],)).fetchone()["state"]
        transition(c, t, ex["case_id"], cur, case_state, "system:executor",
                   detail={"execution_id": ex["execution_id"], "reasons": reasons or [], "verification": verification})


def run_one(ex: dict) -> None:
    t, cid = ex["tenant_id"], ex["case_id"]
    if config.ACTION_MODE != "tag_after_approval":
        _finish(ex, status="refused", case_state="EXECUTION_FAILED", reasons=["alert-only mode: the system never changes resources"])
        return
    with tx(t) as c:
        a = c.execute("select * from approvals where approval_id=%s", (ex["approval_id"],)).fetchone()
        case = c.execute("select * from cases where case_id=%s", (cid,)).fetchone()
        pol = c.execute("select * from policies where policy_id=%s", (ex["policy_id"],)).fetchone()
    now = utcnow()
    bad = []
    if a["status"] != "approved" or case["state"] != "APPROVED":
        bad.append(f"approval is {a['status']} / case is {case['state']}")
    if case["current_version"] != a["case_version"] or pol["case_version"] != a["case_version"]:
        bad.append("case version changed after approval")
    if not (sha256_hex(pol["policy_yaml"]) == pol["policy_sha256"] == a["policy_sha256"] == ex["policy_sha256"]):
        bad.append("policy digest mismatch")
    if not (target_set_hash(pol["target_ids"]) == pol["target_set_sha256"] == a["target_set_sha256"] == ex["target_set_sha256"]):
        bad.append("target-set digest mismatch")
    if now >= a["expires_at"]:
        bad.append("approval expired before execution started")
    if pol["status"] != "valid" or not pol["target_ids"] or len(pol["target_ids"]) > config.MAX_TARGETS:
        bad.append("policy is not valid or exceeds the target cap")
    if bad:
        audit_reason = {"stage": "api_preflight", "reasons": bad}
        _finish(ex, status="refused", case_state="EXECUTION_FAILED", reasons=bad, runner={"log": json_dump(audit_reason)})
        return

    template = pol["template"]
    resource_type = CAPS[template][2]
    grant = {"execution_id": ex["execution_id"], "tenant_id": t, "template": template, "policy_yaml": pol["policy_yaml"],
             "policy_name": f"{cid}-v{pol['case_version']}", "policy_sha256": pol["policy_sha256"],
             "target_ids": pol["target_ids"], "target_set_sha256": pol["target_set_sha256"],
             "expires_at": a["expires_at"].isoformat(), "max_targets": config.MAX_TARGETS, "resource_type": resource_type}
    try:
        r = runner_client.call("/execute", grant, timeout=240)
    except Exception as e:   # timeout / connection loss: outcome unknown -> no blind retry
        _finish(ex, status="inconclusive", case_state="EXECUTION_FAILED",
                reasons=[f"runner call failed ({type(e).__name__}); outcome unknown, not retried: inspect manually"])
        return
    if r["status"] != "succeeded":
        _finish(ex, status="refused" if r["status"] == "refused" else "failed", case_state="EXECUTION_FAILED",
                runner=r, reasons=r.get("reasons") or [f"runner status {r['status']}"])
        return

    # action done -> EXECUTED, then verify against a fresh live read
    with tx(t) as c:
        c.execute("update executions set status='succeeded', ended_at=now(), exit_code=%s, matched_ids=%s, result=%s, logs=%s where execution_id=%s",
                  (r["exit_code"], J(r["matched_ids"]), J({"runner": {k: v for k, v in r.items() if k != "log"}}), r["log"], ex["execution_id"]))
        transition(c, t, cid, "APPROVED", "EXECUTED", "system:executor",
                   detail={"execution_id": ex["execution_id"], "matched_ids": r["matched_ids"], "exit_code": r["exit_code"]})
    verification = verify(template, resource_type, pol["target_ids"], r.get("before", {}))
    with tx(t) as c:
        c.execute("update executions set verification=%s, verified_at=now() where execution_id=%s", (J(verification), ex["execution_id"]))
        transition(c, t, cid, "EXECUTED", "VERIFIED" if verification["ok"] else "VERIFICATION_FAILED", "system:verifier",
                   detail={"execution_id": ex["execution_id"], "verification": verification})


def verify(template: str, resource_type: str, ids: list[str], before: dict) -> dict:
    """Fresh, read-only live query after the action. VERIFIED only if the expected change is actually observed."""
    try:
        live = runner_client.call("/inspect", {"resource_type": resource_type, "ids": ids})["resources"]
    except Exception as e:
        return {"ok": False, "inconclusive": True, "reason": f"post-check query failed: {type(e).__name__}"}
    key, want = ("finops_mark", "stop@") if template == "idle-ec2-v1" else ("finops_review", "unattached-ebs-v1")
    rows, ok = [], True
    for i in ids:
        tags = (live.get(i) or {}).get("tags", {})
        seen = tags.get(key)
        changed = seen is not None and want in seen and key not in before.get(i, {})
        rows.append({"resource_id": i, "expected_tag": key, "expected_contains": want, "observed": seen,
                     "tag_present_before": key in before.get(i, {}), "state": (live.get(i) or {}).get("state"), "changed": changed})
        ok = ok and changed
    return {"ok": ok, "checked_at": utcnow().isoformat(), "resources": rows}


def json_dump(o) -> str:
    import json
    return json.dumps(o)


def loop() -> None:
    recover_stale()
    while True:
        try:
            ex = _claim()
            if ex:
                log.info("executing %s", ex["execution_id"])
                run_one(ex)
                continue
        except Exception:
            log.exception("executor loop error")
        WAKE.wait(1.0)
        WAKE.clear()
