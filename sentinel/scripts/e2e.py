#!/usr/bin/env python3
"""End-to-end proof against the RUNNING stack (stdlib only). Everything it asserts is observed, not simulated:
real n8n webhooks and form, real Custodian run, real LocalStack state read over its EC2 API.

  python3 scripts/e2e.py            # main proof (needs a freshly seeded stack: ./scripts/reset.sh)
  python3 scripts/e2e.py --expiry   # timeout never auto-approves (run with APPROVAL_TTL_SECONDS=25)
  python3 scripts/e2e.py --outbox   # n8n down -> events retried, no loss, no duplicates (needs fresh stack)
"""
import hashlib
import hmac
import json
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV = dict(l.split("=", 1) for l in (ROOT / ".env").read_text().splitlines() if "=" in l and not l.startswith("#"))
API, N8N, LS = "http://localhost:8000", "http://localhost:5678", "http://localhost:4566"
LOG, FAILS = [], []


def say(msg=""):
    print(msg, flush=True)
    LOG.append(msg)


def check(name, ok, detail=""):
    say(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)
    return ok


def http(method, url, headers=None, body=None, timeout=60):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            code = r.status
    except urllib.error.HTTPError as e:
        raw, code = e.read(), e.code
    try:
        return code, json.loads(raw)
    except ValueError:
        return code, raw.decode("utf8", "replace")


def tok(pid):
    return {"authorization": f"Bearer tok_{pid}_demo"}


def get(path, pid):
    return http("GET", API + path, tok(pid))


def case(pid, cid):
    c, d = get(f"/cases/{cid}", pid)
    return d if c == 200 else None


def canonical(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def callback(body, token=None, secret=None, ts=None):
    raw = canonical(body).encode()
    ts = str(ts or int(time.time()))
    sig = hmac.new((secret or ENV["N8N_HMAC_SECRET"]).encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    return http("POST", API + "/approve", {"authorization": f"Bearer {token or ENV['N8N_API_TOKEN']}", "x-sentinel-timestamp": ts,
                                           "x-sentinel-signature": sig, "content-type": "application/json"}, raw)


def submit_form(url, approver, pin, decision, note=""):
    b = uuid.uuid4().hex
    parts = "".join(f'--{b}\r\nContent-Disposition: form-data; name="field-{i}"\r\n\r\n{v}\r\n' for i, v in enumerate([approver, pin, decision, note]))
    return http("POST", url, {"content-type": f"multipart/form-data; boundary={b}"}, (parts + f"--{b}--\r\n").encode())


def wait_for(fn, timeout=120, every=2):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return None


def localstack_tags():
    """Read instance state straight from LocalStack's EC2 API (independent of the platform)."""
    c, xml = http("GET", f"{LS}/?Action=DescribeInstances&Version=2016-11-15",
                  {"authorization": "AWS4-HMAC-SHA256 Credential=test/20260101/us-east-1/ec2/aws4_request, SignedHeaders=host, Signature=x"})
    ns = {"e": "http://ec2.amazonaws.com/doc/2016-11-15"}
    out = {}
    for it in ET.fromstring(xml).iter("{http://ec2.amazonaws.com/doc/2016-11-15}item"):
        iid = it.find("e:instanceId", ns)
        if iid is None:
            continue
        tags = {t.find("e:key", ns).text: t.find("e:value", ns).text for t in it.findall("e:tagSet/e:item", ns)}
        out[iid.text] = {"name": tags.get("Name"), "state": it.find("e:instanceState/e:name", ns).text, "finops_mark": tags.get("finops_mark")}
    return out


def trigger_run():
    for _ in range(30):                      # webhooks register a moment after n8n reports healthy
        code, body = http("POST", f"{N8N}/webhook/sentinel-run-now", {"x-run-now-secret": ENV["RUN_NOW_SECRET"]})
        if code != 404:
            return code, body
        time.sleep(2)
    return code, body


def states(pid):
    return {c["case_id"]: c["state"] for c in get("/cases", pid)[1]}


def pending_ready(pid, cid):
    d = case(pid, cid)
    return d if d and d["state"] == "PENDING" and (d["approval"] or {}).get("form_url") else None


def main():
    say("== 1. trigger analysis through n8n (CloudQuery sync -> API analysis -> outbox -> n8n intake -> Custodian dry-run)")
    t0 = time.time()
    code, _ = trigger_run()
    check("n8n manual webhook accepted", code == 202, f"HTTP {code}")
    pay = wait_for(lambda: pending_ready("payments-analyst", "case_payments_batch-dev-idle-ec2"))
    ana = wait_for(lambda: pending_ready("analytics-analyst", "case_analytics_notebooks-unattached-ebs"))
    took = time.time() - t0
    check("both approval-ready cases reached PENDING with an n8n form", bool(pay and ana), f"{took:.0f}s")
    check("seeded event -> approval-ready under the 5-minute target", took < 300, f"{took:.0f}s")
    if not (pay and ana):
        return
    say("\n== 2. scenario outcomes")
    sp, sa = states("payments-analyst"), states("analytics-analyst")
    for k, v in {**sp, **sa}.items():
        say(f"    {k:52} {v}")
    check("expected Payments spike is DISMISSED as legitimate (not waste)", sp["case_payments_checkout-api-spend-anomaly"] == "DISMISSED")
    check("normal RDS baseline raised no case", not any("ledger" in k for k in sp))
    check("protected production resource REFUSED before approval", sp["case_payments_dr-standby-idle-ec2"] == "REFUSED")
    check("ambiguous-owner case held as needs_review (no dry-run, no approval)", sp["case_payments_shared-ingest-idle-ec2"] == "INVESTIGATED"
          and case("payments-analyst", "case_payments_shared-ingest-idle-ec2")["policy"] is None)
    dr = case("payments-analyst", "case_payments_dr-standby-idle-ec2")
    say(f"    refusal reasons: {dr['policy']['refusal_reasons']} | excluded: {dr['policy']['excluded']}")
    sh = case("payments-analyst", "case_payments_shared-ingest-idle-ec2")
    check("ambiguous case: owner is None, never invented", sh["content"]["attribution"]["owner"] is None, sh["content"]["attribution"]["reason"])

    say("\n== 3. exact dry-run evidence (Payments safe-action candidate)")
    p, a = pay["policy"], pay["approval"]
    say(f"    custodian {p['runner_versions']['custodian']} dry-run exit {p['dryrun_exit_code']}  targets {p['target_ids']}")
    say(f"    policy sha256     {p['policy_sha256']}\n    target-set sha256 {p['target_set_sha256']}\n    projected saving  {p['guards']['savings']['monthly']} USD/month (projected; realized = not measured)")
    check("exactly two sorted targets, hashes bound to the approval", len(p["target_ids"]) == 2 and p["target_ids"] == sorted(p["target_ids"])
          and a["policy_sha256"] == p["policy_sha256"] and a["target_set_sha256"] == p["target_set_sha256"])
    check("policy is a deterministic template (mark-for-op, no delete/terminate)", "mark-for-op" in p["policy_yaml"] and "terminate" not in p["policy_yaml"])
    before = localstack_tags()
    check("LocalStack BEFORE: 6 instances seen, none carries finops_mark", len(before) == 6 and all(v["finops_mark"] is None for v in before.values()), f"{len(before)} instances")

    say("\n== 4. cross-tenant denial (Payments identity vs the Analytics case)")
    aid = "case_analytics_notebooks-unattached-ebs"
    check("Payments cannot read the Analytics case by guessing its id", get(f"/cases/{aid}", "payments-analyst")[0] == 404)
    check("Payments cannot read its audit trail", get(f"/cases/{aid}/audit", "payments-analyst")[0] == 404)
    check("Payments cannot dry-run / act on it", http("POST", f"{API}/cases/{aid}/dry-run", tok("payments-analyst"))[0] == 404)
    aa = ana["approval"]
    forged = {"approval_id": aa["approval_id"], "case_id": aid, "decision": "approve", "actor_id": "payments-approver", "actor_proof": "1111",
              "case_version": ana["version"], "policy_sha256": aa["policy_sha256"], "target_set_sha256": aa["target_set_sha256"],
              "expires_at": aa["expires_at"], "idempotency_key": "forge-" + uuid.uuid4().hex}
    c, r = callback(forged)
    check("validly-signed n8n callback naming a Payments approver for an Analytics approval is refused", c == 403, f"HTTP {c} {r}")
    check("human bearer token cannot call the callback endpoint", http("POST", API + "/approve", {**tok("payments-approver"), "content-type": "application/json"}, b"{}")[0] == 403)
    check("bad signature rejected", callback(forged, secret="wrong")[0] == 401)
    check("stale timestamp rejected (replay window)", callback(forged, ts=int(time.time()) - 1000)[0] == 401)
    check("Analytics case still PENDING after all forged attempts", states("analytics-analyst")[aid] == "PENDING")

    say("\n== 5. human approval through the n8n form (first with a WRONG pin)")
    form = a["form_url"]
    c, _ = submit_form(form, "payments-approver", "0000", "approve")
    time.sleep(4)
    check("wrong PIN does not approve", case("payments-analyst", "case_payments_batch-dev-idle-ec2")["state"] == "PENDING", f"form HTTP {c}")
    aud = [x["detail"].get("reason") for x in case("payments-analyst", "case_payments_batch-dev-idle-ec2")["audit"] if x["event_type"] == "approval.callback_refused"]
    check("refusal recorded in the tenant audit trail", bool(aud), str(aud))
    say("   now the real approver submits the same form")
    c, _ = submit_form(form, "payments-approver", "1111", "approve", "e2e proof")
    check("form accepted", c == 200, f"HTTP {c}")
    done = wait_for(lambda: (lambda d: d if d and d["state"] in ("VERIFIED", "VERIFICATION_FAILED", "EXECUTION_FAILED") else None)(case("payments-analyst", "case_payments_batch-dev-idle-ec2")), 120)
    check("case reached VERIFIED", bool(done) and done["state"] == "VERIFIED", done["state"] if done else "timeout")
    ex = done["execution"]
    say(f"    execution {ex['execution_id']} status={ex['status']} exit={ex['exit_code']} matched={ex['matched_ids']}")
    say(f"    approver={done['approval']['actor_id']} role={done['approval']['actor_role']} decided={done['approval']['decided_at']}")
    for r in ex["verification"]["resources"]:
        say(f"    verify {r['resource_id']}: {r['expected_tag']} = {r['observed']!r} (changed={r['changed']})")
    check("runner matched exactly the approved targets, exit 0", ex["matched_ids"] == p["target_ids"] and ex["exit_code"] == 0)

    say("\n== 6. live resource state in LocalStack AFTER the action (independent read)")
    after = localstack_tags()
    for iid, v in after.items():
        say(f"    {iid} {v['name']:24} {v['state']:8} finops_mark={v['finops_mark']}")
    marked = sorted(i for i, v in after.items() if v["finops_mark"])
    check("exactly the two approved instances are marked, and still running (non-destructive)", marked == p["target_ids"]
          and all(after[i]["state"] == "running" for i in after))
    check("protected DR instance untouched", all(v["finops_mark"] is None for v in after.values() if v["name"] == "payments-dr-standby"))

    say("\n== 7. duplicate callback / replay does nothing")
    key = f"{a['approval_id']}:approve:payments-approver"          # the key n8n derived for the real click
    dup = {**forged, "approval_id": a["approval_id"], "case_id": "case_payments_batch-dev-idle-ec2", "actor_id": "payments-approver",
           "actor_proof": "1111", "case_version": pay["version"], "policy_sha256": a["policy_sha256"], "target_set_sha256": a["target_set_sha256"],
           "expires_at": a["expires_at"], "idempotency_key": key}
    c, r = callback(dup)
    check("same idempotency key returns the earlier result, flagged replayed", c == 200 and r.get("replayed") is True, f"HTTP {c} {r}")
    c, r = callback({**dup, "idempotency_key": "another-" + uuid.uuid4().hex})
    check("fresh key on an already-decided approval is refused (no second execution)", c == 409, f"HTTP {c} {r.get('error') if isinstance(r, dict) else r}")
    c, _ = submit_form(form, "payments-approver", "1111", "approve")
    time.sleep(3)
    d2 = case("payments-analyst", "case_payments_batch-dev-idle-ec2")
    check("re-clicking the n8n form is a no-op", d2["execution"]["execution_id"] == ex["execution_id"] and d2["state"] == "VERIFIED", f"form HTTP {c}")
    check("LocalStack unchanged by the duplicates", localstack_tags() == after)
    n_exec = int(subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "sentinel", "-d", "sentinel", "-At", "-c",
                                 "select count(*) from executions where case_id='case_payments_batch-dev-idle-ec2'"], cwd=ROOT, capture_output=True, text=True).stdout.strip() or -1)
    check("database holds exactly ONE execution for the case", n_exec == 1, str(n_exec))

    say("\n== 8. rejection closes the gate (Analytics)")
    c, _ = submit_form(aa and ana["approval"]["form_url"], "analytics-approver", "3333", "reject", "not now")
    rej = wait_for(lambda: (lambda d: d if d["state"] == "REJECTED" else None)(case("analytics-analyst", aid)), 30)
    check("Analytics case REJECTED, nothing executed", bool(rej) and rej["execution"] is None)

    say("\n== 9. tenant-scoped audit export")
    rows = [json.loads(l) for l in http("GET", API + "/audit/export", tok("payments-analyst"))[1].splitlines() if l]
    check("Payments export contains only Payments events", {r["tenant_id"] for r in rows} == {"payments"}, f"{len(rows)} events")
    say("\n   timeline (batch-dev):")
    for x in done["audit"]:
        say(f"     {x['created_at'][11:23]} {x['event_type']:26} {x['from_state'] or '':12}-> {x['to_state'] or '':12} {x['actor']}")


def expiry():
    say("== expiry: no decision => approval EXPIRED, never auto-approved (requires APPROVAL_TTL_SECONDS small)")
    trigger_run()
    pay = wait_for(lambda: pending_ready("payments-analyst", "case_payments_batch-dev-idle-ec2"))
    check("PENDING with form", bool(pay))
    ttl = (datetime.fromisoformat(pay["approval"]["expires_at"]) - datetime.fromisoformat(pay["approval"]["requested_at"])).total_seconds()
    say(f"   approval TTL = {ttl:.0f}s; waiting for expiry without deciding")
    done = wait_for(lambda: (lambda d: d if d["state"] != "PENDING" else None)(case("payments-analyst", "case_payments_batch-dev-idle-ec2")), ttl + 90, 3)
    check("case EXPIRED", bool(done) and done["state"] == "EXPIRED", done["state"] if done else "timeout")
    check("approval expired and no execution exists", done["approval"]["status"] == "expired" and done["execution"] is None)
    c, _ = submit_form(pay["approval"]["form_url"], "payments-approver", "1111", "approve")
    time.sleep(3)
    d = case("payments-analyst", "case_payments_batch-dev-idle-ec2")
    check("a late click after expiry cannot approve", d["state"] == "EXPIRED" and d["execution"] is None, f"form HTTP {c}")
    check("nothing marked in LocalStack", all(v["finops_mark"] is None for v in localstack_tags().values()))
    say("   escalation trail: " + str([x["event_type"] for x in d["audit"] if "approval" in x["event_type"] or "escal" in x["event_type"]]))


def outbox():
    say("== outbox: n8n DOWN during analysis -> nothing lost, delivered later, no duplicates")
    subprocess.run(["docker", "compose", "stop", "n8n"], cwd=ROOT, capture_output=True)
    c, sync = http("POST", "http://localhost:8080/sync", {"authorization": "Bearer x"}) if False else (0, None)
    r = subprocess.run(["docker", "compose", "exec", "-T", "cq-runner", "python", "-c",
                        "import urllib.request,json,os;r=urllib.request.Request('http://localhost:8080/sync',method='POST',headers={'Authorization':'Bearer '+os.environ['CQ_RUNNER_TOKEN']});print(urllib.request.urlopen(r,timeout=600).read().decode())"],
                       cwd=ROOT, capture_output=True, text=True)
    sync = json.loads(r.stdout)
    c, out = http("POST", API + "/internal/analyze", {"authorization": f"Bearer {ENV['N8N_API_TOKEN']}", "content-type": "application/json"},
                  json.dumps({"tenant_ids": ["payments", "analytics"], "idempotency_prefix": "outbox-test", "sync": {k: sync[k] for k in ("sync_id", "started_at", "finished_at", "plugin_versions", "exit_code")}}).encode())
    check("analysis committed while n8n is down", c == 200, f"HTTP {c}")
    check("cases exist (not rolled back by n8n downtime)", len(states("payments-analyst")) >= 4)
    time.sleep(8)
    q = "select count(*) filter (where delivered_at is null), max(attempts), count(*) from outbox"
    res = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "sentinel", "-d", "sentinel", "-At", "-F", ",", "-c", q], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    undelivered, attempts, total = (int(x) for x in res.split(","))
    check("events are queued and retrying", undelivered == total and attempts >= 1, res)
    check("no case advanced without n8n", set(states("payments-analyst").values()) <= {"INVESTIGATED", "DISMISSED"})
    subprocess.run(["docker", "compose", "start", "n8n"], cwd=ROOT, capture_output=True)
    ok = wait_for(lambda: pending_ready("payments-analyst", "case_payments_batch-dev-idle-ec2"), 240, 4)
    check("after n8n returns the backlog is delivered and the case reaches PENDING", bool(ok))
    q2 = ("select (select count(*) from cases), (select count(*) from policies), (select count(*) from approvals),"
          " (select count(*) from (select case_id from policies group by case_id having count(*) > 1) a),"
          " (select count(*) from outbox where delivered_at is null)")
    res = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "sentinel", "-d", "sentinel", "-At", "-c", q2], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    say(f"   cases,policies(2 valid + 1 refused),approvals,cases-with-duplicate-policy,undelivered-events = {res}")
    cs, pol, apr, dup, undel = (int(x) for x in res.split("|"))
    check("retries created no duplicate cases/policies/approvals; outbox fully drained", (cs, pol, apr, dup, undel) == (5, 3, 2, 0, 0), res)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    {"--expiry": expiry, "--outbox": outbox}.get(mode, main)()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (ROOT / "proof").mkdir(exist_ok=True)
    (ROOT / "proof" / f"e2e{mode.replace('--', '-')}-{stamp}.log").write_text("\n".join(LOG) + "\n")
    say(f"\nRESULT: {'ALL CHECKS PASSED' if not FAILS else 'FAILED: ' + '; '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)
