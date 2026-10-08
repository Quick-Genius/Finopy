"""POST /approve (v2) callback logic.

The caller is n8n (automation principal + HMAC). Everything that decides the outcome is read from OUR database:
tenant, targets, policy, hashes, expiry and state. The payload only names the approval and echoes the values the
approver saw; any difference from the stored values is a refusal.
"""
import hmac
import json
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, ValidationError

from . import config
from .db import J, tx
from .security import canonical, hash_pin, sha256_hex, utcnow, verify
from .states import StateConflict, audit, transition

TIMEOUT_ACTOR = "system:n8n-timeout"


class CallbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_id: str
    case_id: str
    decision: str            # approve | reject | timeout
    actor_id: str
    actor_proof: str | None = None     # approver PIN for the local n8n form (stand-in for Slack identity)
    case_version: int
    policy_sha256: str
    target_set_sha256: str
    expires_at: datetime
    idempotency_key: str


class Refusal(Exception):
    def __init__(self, code: int, reason: str, *, record_state: bool = False):
        self.code, self.reason, self.record_state = code, reason, record_state


def handle(raw: bytes, ts: str, signature: str) -> tuple[int, dict]:
    if not verify(config.N8N_HMAC_SECRET, ts, raw, signature, config.CALLBACK_REPLAY_WINDOW_SECONDS):
        return 401, {"error": "bad_signature_or_stale_timestamp"}
    try:
        parsed = json.loads(raw)
        req = CallbackIn(**parsed)
    except (ValidationError, ValueError, TypeError) as e:
        return 422, {"error": "invalid_body", "detail": str(e)[:300]}
    req_hash = sha256_hex(canonical(parsed))      # canonical form: formatting differences in a retry must not defeat idempotency

    # 1. idempotency: same key + same body -> the earlier result, no side effects
    with tx(system=True) as c:
        prior = c.execute("select * from idempotency_keys where key=%s", (req.idempotency_key,)).fetchone()
        row = c.execute("select a.*, cs.state, cs.current_version from approvals a join cases cs on cs.case_id=a.case_id "
                        "where a.approval_id=%s and a.case_id=%s", (req.approval_id, req.case_id)).fetchone()
    if prior:
        if prior["request_sha256"] != req_hash:
            return 409, {"error": "idempotency_key_reused_with_different_request"}
        return prior["status_code"], {**prior["response"], "replayed": True}
    if not row:
        return 404, {"error": "approval_not_found"}
    tenant = row["tenant_id"]                       # server-side tenant, from the stored approval

    try:
        with tx(tenant) as c:
            a = c.execute("select * from approvals where approval_id=%s for update", (req.approval_id,)).fetchone()
            case = c.execute("select * from cases where case_id=%s for update", (req.case_id,)).fetchone()
            now = utcnow()
            try:
                result = _decide(c, req, a, case, tenant, now)
            except Refusal as r:
                # return (not raise) so the refusal's audit row commits; refusals do not consume the idempotency key
                audit(c, tenant, req.case_id, "approval.callback_refused", req.actor_id, version=case["current_version"],
                      detail={"approval_id": req.approval_id, "decision": req.decision, "reason": r.reason, "http": r.code})
                return r.code, {"error": r.reason}
            c.execute("insert into idempotency_keys (key, tenant_id, request_sha256, status_code, response) values (%s,%s,%s,%s,%s)",
                      (req.idempotency_key, tenant, req_hash, 200, J(result)))
            return 200, result
    except StateConflict as e:
        return 409, {"error": "state_conflict", "detail": str(e)}


def _decide(c, req: CallbackIn, a, case, tenant, now) -> dict:
    cid = req.case_id
    # actor must be a principal of THIS case's tenant with the approver role (timeout is the only actor-less path)
    if req.decision not in ("approve", "reject", "timeout"):
        raise Refusal(422, "unknown decision")
    if req.decision == "timeout":
        if req.actor_id != TIMEOUT_ACTOR:
            raise Refusal(403, "timeout must come from the workflow timeout actor")
        role = "system"
    else:
        with tx(system=True) as sc:
            p = sc.execute("select * from principals where principal_id=%s", (req.actor_id,)).fetchone()
        if not p or p["tenant_id"] != tenant:
            raise Refusal(403, "actor is not a member of the case's tenant")
        if p["role"] not in ("approver", "reviewer"):
            raise Refusal(403, f"role {p['role']} may not decide this approval")
        if not p["pin_hash"] or not req.actor_proof or not hmac.compare_digest(p["pin_hash"], hash_pin(req.actor_proof)):
            raise Refusal(403, "actor identity proof failed")
        role = p["role"]

    if a["status"] != "requested" or case["state"] != "PENDING":
        raise Refusal(409, f"approval is {a['status']} and case is {case['state']}; nothing to decide")
    if req.case_version != case["current_version"] or req.case_version != a["case_version"]:
        raise Refusal(409, "case version changed since the approver saw it")
    if req.policy_sha256 != a["policy_sha256"] or req.target_set_sha256 != a["target_set_sha256"]:
        raise Refusal(409, "policy or target-set digest does not match the frozen approval")
    pol = c.execute("select policy_yaml, policy_sha256, target_ids, target_set_sha256 from policies where policy_id=%s", (a["policy_id"],)).fetchone()
    from .security import target_set_hash
    if sha256_hex(pol["policy_yaml"]) != a["policy_sha256"] or target_set_hash(pol["target_ids"]) != a["target_set_sha256"]:
        raise Refusal(409, "stored policy no longer hashes to the approved digests")
    if req.expires_at != a["expires_at"]:
        raise Refusal(409, "expiry does not match the approval")

    expired = now >= a["expires_at"]
    if req.decision == "timeout":
        if not expired:
            raise Refusal(409, "approval has not reached its expiry")
    elif expired:
        _close(c, a, case, tenant, "expired", "EXPIRED", req.actor_id, role, "decision arrived after expiry")
        return {"outcome": "expired", "state": "EXPIRED", "approval_id": a["approval_id"], "note": "decision arrived after expiry; nothing executed"}

    if req.decision == "timeout":
        _close(c, a, case, tenant, "expired", "EXPIRED", req.actor_id, role, "approval timed out; escalated to platform reviewer, never auto-approved")
        audit(c, tenant, cid, "approval.escalated", req.actor_id, version=case["current_version"],
              detail={"approval_id": a["approval_id"], "to": "platform reviewer (dashboard queue)"})
        return {"outcome": "expired", "state": "EXPIRED", "approval_id": a["approval_id"]}
    if req.decision == "reject":
        _close(c, a, case, tenant, "rejected", "REJECTED", req.actor_id, role, "rejected by approver")
        return {"outcome": "rejected", "state": "REJECTED", "approval_id": a["approval_id"]}

    c.execute("update approvals set status='approved', decided_at=%s, actor_id=%s, actor_role=%s where approval_id=%s",
              (now, req.actor_id, role, a["approval_id"]))
    transition(c, tenant, cid, "PENDING", "APPROVED", req.actor_id, detail={"approval_id": a["approval_id"], "role": role,
              "policy_sha256": a["policy_sha256"], "target_set_sha256": a["target_set_sha256"]})
    xid = "exe_" + uuid.uuid4().hex[:12]
    c.execute("""insert into executions (execution_id, case_id, tenant_id, approval_id, status, policy_id, policy_sha256, target_set_sha256)
                 values (%s,%s,%s,%s,'queued',%s,%s,%s)""",
              (xid, cid, tenant, a["approval_id"], a["policy_id"], a["policy_sha256"], a["target_set_sha256"]))
    audit(c, tenant, cid, "execution.queued", "system", version=case["current_version"], detail={"execution_id": xid})
    return {"outcome": "approved", "state": "APPROVED", "approval_id": a["approval_id"], "execution_id": xid}


def _close(c, a, case, tenant, status, to_state, actor, role, note):
    c.execute("update approvals set status=%s, decided_at=now(), actor_id=%s, actor_role=%s, note=%s where approval_id=%s",
              (status, actor, role, note, a["approval_id"]))
    transition(c, tenant, case["case_id"], "PENDING", to_state, actor, detail={"approval_id": a["approval_id"], "note": note})
