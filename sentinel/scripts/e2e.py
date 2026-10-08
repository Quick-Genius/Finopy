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
import re
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
PROFILE = ENV.get("SEED_PROFILE", "problem")
ALERT_ONLY = ENV.get("ACTION_MODE", "alert_only") != "tag_after_approval"     # the default: the system never changes anything
API = f"http://localhost:{ENV.get('API_HOST_PORT', '8000')}"
N8N = f"http://localhost:{ENV.get('N8N_HOST_PORT', '5678')}"
LS = f"http://localhost:{ENV.get('LOCALSTACK_HOST_PORT', '4566')}"
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
    """POST the n8n approval form. Retries only if the form is not yet waiting (non-200 / connection error)."""
    b = uuid.uuid4().hex
    parts = "".join(f'--{b}\r\nContent-Disposition: form-data; name="field-{i}"\r\n\r\n{v}\r\n' for i, v in enumerate([approver, pin, decision, note]))
    body = (parts + f"--{b}--\r\n").encode()
    last = (0, "")
    for _ in range(8):
        try:
            last = http("POST", url, {"content-type": f"multipart/form-data; boundary={b}"}, body)
        except Exception as ex:                       # connection reset while n8n is registering the wait
            last = (0, str(ex))
        if last[0] == 200:
            return last
        time.sleep(1.5)
    return last


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



def deliver(path, ev):
    """Deliver a correctly SIGNED event to an n8n webhook, exactly as the API outbox does. Returns (status, body)."""
    raw = canonical(ev).encode()
    ts = str(int(time.time()))
    sig = hmac.new(ENV["N8N_HMAC_SECRET"].encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    return http("POST", f"{N8N}/webhook/{path}", {"content-type": "application/json", "x-sentinel-timestamp": ts, "x-sentinel-signature": sig}, raw)


def trigger_run():
    for _ in range(30):                      # webhooks register a moment after n8n reports healthy
        code, body = http("POST", f"{N8N}/webhook/sentinel-run-now", {"x-run-now-secret": ENV["RUN_NOW_SECRET"]})
        if code != 404:
            return code, body
        time.sleep(2)
    return code, body


def states(pid):
    return {c["case_id"]: c["state"] for c in get("/cases", pid)[1]}


def case_when_listed(pid, cid):
    """Poll the list (always 200) and fetch the case only once it exists, so waiting never produces a 404."""
    c, lst = get("/cases", pid)
    return case(pid, cid) if c == 200 and any(r["case_id"] == cid for r in lst) else None


def pending_ready(pid, cid):
    d = case_when_listed(pid, cid)
    return d if d and d["state"] == "PENDING" and (d["approval"] or {}).get("form_url") else None


def slack_check():
    """Only runs when scripts/slack_mock.sh is up AND n8n was started with SLACK_WEBHOOK_URL pointing at it."""
    r = subprocess.run(["docker", "exec", "slack-mock", "cat", "/tmp/slack.log"], capture_output=True, text=True)
    if r.returncode:
        say("\n== 10. Slack: skipped (no slack-mock running; set SLACK_WEBHOOK_URL to test)")
        return
    time.sleep(6)
    msgs = [json.loads(l)["text"] for l in subprocess.run(["docker", "exec", "slack-mock", "cat", "/tmp/slack.log"], capture_output=True, text=True).stdout.splitlines() if l.strip()]
    say(f"\n== 10. Slack notifications received by the stand-in webhook ({len(msgs)})")
    for m in msgs:
        say("    --- " + m.replace("\n", "\n        "))
    need = {"approval needed": "Approval needed", "needs review": "Needs human review", "REFUSED": "*REFUSED*", "APPROVED": "*APPROVED*", "REJECTED": "*REJECTED*"}
    if not ALERT_ONLY:
        need["VERIFIED"] = "*VERIFIED*"
    else:
        check("alert-only: the APPROVED message says the system made NO change", "NO change" in next((m for m in msgs if "*APPROVED*" in m), ""))
        check("alert-only: nothing was ever announced as executed or verified", not any("*VERIFIED*" in m or "*EXECUTED*" in m for m in msgs if "slack-ping" not in m))
    for name, needle in need.items():
        check(f"Slack got a '{name}' message", any(needle in m for m in msgs))
    sections = ("*Issue*", "*Has to be fixed*", "*Recommendation*", "*Stats*", "```", "*Who acts:*", "Alert only")
    by = lambda needle: next((m for m in msgs if needle in m), "")
    for label, needle in (("approval-needed", "Approval needed"), ("needs-review", "Needs human review"), ("REFUSED", "*REFUSED*")):
        missing = [s for s in sections if s not in by(needle)]
        check(f"{label} message is structured: Issue / Has to be fixed / Recommendation / Stats / Who acts", not missing, f"missing {missing}")
    check("approval message includes the text charts (cost sparkline + CPU bars)", "cost/day (14d)" in by("Approval needed") and "CPU " in by("Approval needed") and "█" in by("Approval needed"))
    check("every message says it is an alert (nothing fixed automatically)", all("Alert only" in m for m in msgs if "slack-ping" not in m), f"{sum('Alert only' in m for m in msgs)} of {len(msgs)}")
    check("short outcome messages (APPROVED / VERIFIED) stay short: no full briefing", "*Stats*" not in by("*APPROVED*") and "*Stats*" not in by("*VERIFIED*"))
    check("approval message carries the n8n form link", any("approval form" in m and "/form-waiting/" in m for m in msgs))
    check("no secrets in any Slack message (PIN, HMAC secret, API token)", not any(s in "\n".join(msgs) for s in ("1111", "3333", ENV["N8N_HMAC_SECRET"], ENV["N8N_API_TOKEN"])))
    llm = subprocess.run(["docker", "exec", "llm-mock", "cat", "/tmp/llm.log"], capture_output=True, text=True)
    if llm.returncode == 0 and llm.stdout.strip():
        reqs = llm.stdout
        say(f"    LLM stand-in received {len(reqs.splitlines())} requests")
        check("Slack messages carry an investigation summary written by the LLM", sum("In short" in m and "AI-written" in m for m in msgs) >= 5, f"{sum('AI-written' in m for m in msgs)} of {len(msgs)}")
        leaked = [s for s in ("1111", "3333", "policy_yaml", "sha256", "actor_proof", ENV["N8N_HMAC_SECRET"], ENV["N8N_API_TOKEN"], ENV["RUNNER_HMAC_SECRET"]) if s in reqs]
        check("nothing secret was sent to the LLM (PINs, hashes, policy text, tokens, secrets)", not leaked, str(leaked))
    refs = [r for m in msgs for r in re.findall(r"_ref (evt_[A-Za-z0-9_]+)_", m)]
    check("every Slack message carries a ref id", len(refs) == len([m for m in msgs if "slack-ping" not in m]), f"{len(refs)} refs / {len(msgs)} messages")
    check("no Slack message was delivered twice (all refs unique)", len(set(refs)) == len(refs), f"{len(refs)} refs, {len(set(refs))} unique")
    check("each outcome was announced exactly once", (ALERT_ONLY or sum("*VERIFIED*" in m for m in msgs) == 1) and sum("*REFUSED*" in m for m in msgs) == 1, f"{len(msgs)} messages")


def slack_ping():
    say("== Slack ping: deliver one signed test event to the notify workflow (appears in your channel if SLACK_WEBHOOK_URL is set)")
    ev = {"event_id": "evt_slackping_" + uuid.uuid4().hex[:10], "event_type": "case.state_changed", "schema_version": "1.0", "tenant_id": "payments",
          "case_id": "case_payments_slack-ping-test", "case_version": 1, "from_state": "EXECUTED", "to_state": "VERIFIED", "actor": "system:slack-ping",
          "detail": {"verification_ok": True, "matched_ids": ["i-0000000000000000"]},
          "summary": {"workload": "slack-ping (TEST, not a real case)", "owner": "payments-platform", "risk": "low", "projected_monthly": "0.0000", "currency": "USD"}}
    code, body = deliver("sentinel-notify", ev)
    check("notify workflow handled the test event", code == 200 and isinstance(body, dict) and body.get("handled") is True, f"HTTP {code} {body}")
    say("   If Slack is configured you should see a message in the channel within a second or two.")


def main():
    say("== 1. trigger analysis through n8n (CloudQuery sync -> API analysis -> outbox -> n8n intake -> Custodian dry-run)")
    t0 = time.time()
    code, _ = trigger_run()
    check("n8n manual webhook answers HTTP 200", code == 200, f"HTTP {code}")
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
    check("over-budget alert raised for Payments (spend above its monthly budget), none for Analytics",
          "case_payments_all-services-budget-overrun" in sp and not any("budget" in k for k in sa), str([k for k in {**sp, **sa} if "budget" in k]))
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

    say("\n== 4b. n8n-side failures must answer 5xx (outbox retries) and must NOT be marked as handled")
    bogus_case = {"event_id": "evt_bogus_case_" + uuid.uuid4().hex[:8], "event_type": "case.created", "case_id": "case_payments_does-not-exist", "tenant_id": "payments"}
    c1, _ = deliver("sentinel-case", bogus_case)
    c2, _ = deliver("sentinel-case", bogus_case)
    check("intake: API fetch fails -> HTTP 500, not a silent 200", c1 == 500, f"HTTP {c1}")
    check("intake: the same event is retried (not swallowed as a duplicate)", c2 == 500, f"HTTP {c2}")
    bogus_apr = {"event_id": "evt_bogus_apr_" + uuid.uuid4().hex[:8], "event_type": "approval.requested", "approval_id": "apr_0123456789ab", "tenant_id": "payments", "case_id": "case_payments_does-not-exist"}
    a1, _ = deliver("sentinel-approval", bogus_apr)
    a2, _ = deliver("sentinel-approval", bogus_apr)
    check("approval: packet fetch fails -> HTTP 500 and the event stays retryable", (a1, a2) == (500, 500), f"HTTP {a1}/{a2}")
    evil = {"event_id": "evt_malformed_" + uuid.uuid4().hex[:8], "event_type": "case.created", "case_id": "case_x/../../internal/events/evt_x", "tenant_id": "payments"}
    m1, mb = deliver("sentinel-case", evil)
    check("signed event with a path-traversal case_id is rejected before any API call", m1 == 401 and "malformed" in str(mb), f"HTTP {m1} {mb}")
    for bad in ("/cases/bad.id", "/cases/case_x%20y", "/approvals/apr_XYZ", "/approvals/..%2Fcases"):
        check(f"API refuses malformed path {bad}", get(bad, "payments-analyst")[0] in (404, 422), f"HTTP {get(bad, 'payments-analyst')[0]}")
    # n8n's seen-set lives in workflow static data, saved whole at the end of each execution, so under concurrent delivery it is
    # best-effort. Sequential delivery of a side-effect-free event (the dismissed case = digest-only route) proves the mechanism.
    safe = {"event_id": "evt_dedupe_probe_" + uuid.uuid4().hex[:8], "event_type": "case.created", "case_id": "case_payments_checkout-api-spend-anomaly", "tenant_id": "payments"}
    d1, b1 = deliver("sentinel-case", safe)
    time.sleep(1)
    d2, b2 = deliver("sentinel-case", safe)
    check("intake: first delivery of a digest-only event is handled (200, no action)", d1 == 200 and isinstance(b1, dict) and b1.get("handled") is True, f"HTTP {d1} {b1}")
    check("intake: sequential re-delivery of the same event_id is a duplicate no-op", d2 == 200 and isinstance(b2, dict) and b2.get("duplicate") is True, f"HTTP {d2} {b2}")

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
    if ALERT_ONLY:
        done = wait_for(lambda: (lambda d: d if d and d["state"] == "APPROVED" else None)(case("payments-analyst", "case_payments_batch-dev-idle-ec2")), 60)
        check("approval recorded: case is APPROVED with the human's identity on record", bool(done) and done["approval"]["actor_id"] == "payments-approver", done["state"] if done else "timeout")
        check("ALERT-ONLY: no execution was created and nothing was run", done["execution"] is None)
        say(f"    approver={done['approval']['actor_id']} role={done['approval']['actor_role']} decided={done['approval']['decided_at']}")
        say("\n== 6. live resource state in LocalStack must be UNTOUCHED (alert-only: the system fixes nothing)")
        after = localstack_tags()
        for iid, v in after.items():
            say(f"    {iid} {v['name']:24} {v['state']:8} finops_mark={v['finops_mark']}")
        check("no instance was changed or tagged, all still running", all(v["finops_mark"] is None and v["state"] == "running" for v in after.values()))
        r = subprocess.run(["docker", "compose", "exec", "-T", "api", "python", "-c",
                            "import httpx\nfrom app import runner_client\ntry:\n runner_client.call('/execute', {'target_ids': [], 'policy_yaml': 'x'})\nexcept httpx.HTTPStatusError as e:\n print('HTTP', e.response.status_code)"],
                           cwd=ROOT, capture_output=True, text=True)
        check("the isolated runner itself refuses to execute in alert-only mode", "HTTP 403" in r.stdout, r.stdout.strip() or r.stderr.strip()[-120:])
        say("\n== 7. duplicate callback / replay does nothing")
        key = f"{a['approval_id']}:approve:payments-approver"
        dup = {**forged, "approval_id": a["approval_id"], "case_id": "case_payments_batch-dev-idle-ec2", "actor_id": "payments-approver",
               "actor_proof": "1111", "case_version": pay["version"], "policy_sha256": a["policy_sha256"], "target_set_sha256": a["target_set_sha256"],
               "expires_at": a["expires_at"], "idempotency_key": key}
        c, r = callback(dup)
        check("same idempotency key returns the earlier result, flagged replayed", c == 200 and r.get("replayed") is True, f"HTTP {c} {r}")
        c, r = callback({**dup, "idempotency_key": "another-" + uuid.uuid4().hex})
        check("fresh key on an already-decided approval is refused", c == 409, f"HTTP {c}")
        c, _ = submit_form(form, "payments-approver", "1111", "approve")
        time.sleep(3)
        d2 = case("payments-analyst", "case_payments_batch-dev-idle-ec2")
        check("re-clicking the n8n form is a no-op", d2["state"] == "APPROVED" and d2["execution"] is None, f"form HTTP {c}")
        n_exec = int(subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "sentinel", "-d", "sentinel", "-At", "-c",
                                     "select count(*) from executions"], cwd=ROOT, capture_output=True, text=True).stdout.strip() or -1)
        check("database holds ZERO executions in total", n_exec == 0, str(n_exec))
    else:
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
    slack_check()
    say("\n   timeline (batch-dev):")
    for x in done["audit"]:
        say(f"     {x['created_at'][11:23]} {x['event_type']:26} {x['from_state'] or '':12}-> {x['to_state'] or '':12} {x['actor']}")


def healthy():
    say("== HEALTHY PROFILE: nothing is wrong, so the only output is an 'all clear' cost digest per tenant (no cases, no recommendations)")
    check("seed profile is healthy", PROFILE == "healthy", f"SEED_PROFILE={PROFILE} (run ./scripts/reset.sh healthy first)")
    code, _ = trigger_run()
    check("run-now answers HTTP 200", code == 200, f"HTTP {code}")
    sql = lambda q: subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "sentinel", "-d", "sentinel", "-At", "-c", q], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    delivered = wait_for(lambda: sql("select count(*) from outbox where event_type='digest.healthy' and delivered_at is not null") == "2", 120, 3)
    check("one 'all clear' digest per tenant was created and delivered through n8n", bool(delivered))
    check("no cases exist for either tenant", get("/cases", "payments-analyst")[1] == [] and get("/cases", "analytics-analyst")[1] == [])
    check("no alert, policy, approval or execution anywhere", sql("select (select count(*) from policies)+(select count(*) from approvals)+(select count(*) from executions)+(select count(*) from outbox where event_type<>'digest.healthy')") == "0")
    check("LocalStack untouched", all(v["finops_mark"] is None and v["state"] == "running" for v in localstack_tags().values()))
    check("every queued event was delivered (outbox empty)", sql("select count(*) from outbox where delivered_at is null") == "0")
    r = subprocess.run(["docker", "exec", "slack-mock", "cat", "/tmp/slack.log"], capture_output=True, text=True)
    if r.returncode == 0:
        time.sleep(4)
        msgs = [json.loads(l)["text"] for l in subprocess.run(["docker", "exec", "slack-mock", "cat", "/tmp/slack.log"], capture_output=True, text=True).stdout.splitlines() if l.strip()]
        say(f"   Slack stand-in received {len(msgs)} message(s):")
        for m in msgs:
            say("     --- " + m.replace("\n", "\n         "))
        check("Slack got exactly two messages: an all-clear for each tenant", len(msgs) == 2 and all("Cost check: all clear" in m for m in msgs))
        check("the digests contain costs and a budget chart, and NO recommendations", all("budget (month)" in m and "Last 30 days" in m and "*Recommendation*" not in m and "*Issue*" not in m for m in msgs))
        check("30-day spend is not $0.00 and is not an empty bar (regression: RLS hid rows outside the tx)", all("Last 30 days: $0.00" not in m and "30 days spent   ░" not in m for m in msgs))
    time.sleep(2)


def happy():
    say("== HAPPY PATH ONLY (no negative tests): every HTTP call made by the automation should be 2xx")
    code, _ = trigger_run()
    check("run-now answers HTTP 200", code == 200, f"HTTP {code}")
    pay = wait_for(lambda: pending_ready("payments-analyst", "case_payments_batch-dev-idle-ec2"))
    ana = wait_for(lambda: pending_ready("analytics-analyst", "case_analytics_notebooks-unattached-ebs"))
    check("both approval-ready cases reached PENDING with an n8n form", bool(pay and ana))
    if not (pay and ana):
        return
    c, _ = submit_form(pay["approval"]["form_url"], "payments-approver", "1111", "approve", "happy path")
    check("Payments approver submits the form (HTTP 200)", c == 200, f"HTTP {c}")
    want = "APPROVED" if ALERT_ONLY else "VERIFIED"
    done = wait_for(lambda: (lambda d: d if d and d["state"] == want else None)(case("payments-analyst", "case_payments_batch-dev-idle-ec2")), 120)
    check(f"Payments case reached {want}", bool(done), done["state"] if done else "timeout")
    c, _ = submit_form(ana["approval"]["form_url"], "analytics-approver", "3333", "reject", "not needed")
    check("Analytics approver submits the form (HTTP 200)", c == 200, f"HTTP {c}")
    rej = wait_for(lambda: (lambda d: d if d["state"] == "REJECTED" else None)(case("analytics-analyst", "case_analytics_notebooks-unattached-ebs")), 60)
    check("Analytics case reached REJECTED", bool(rej))
    drained = wait_for(lambda: subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "sentinel", "-d", "sentinel", "-At", "-c",
                                               "select count(*) from outbox where delivered_at is null"], cwd=ROOT, capture_output=True, text=True).stdout.strip() == "0", 120, 3)
    check("every event was delivered (outbox empty)", bool(drained))
    time.sleep(8)           # let the last Slack posts finish before the log audit


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
    drain = lambda: subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "sentinel", "-d", "sentinel", "-At", "-c",
                                    "select count(*) from outbox where delivered_at is null"], cwd=ROOT, capture_output=True, text=True).stdout.strip() == "0"
    check("the backlog drains completely (every queued event is eventually delivered)", bool(wait_for(drain, 120, 3)))
    q2 = ("select (select count(*) from cases), (select count(*) from policies), (select count(*) from approvals),"
          " (select count(*) from (select case_id from policies group by case_id having count(*) > 1) a),"
          " (select count(*) from outbox where delivered_at is null)")
    res = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "sentinel", "-d", "sentinel", "-At", "-c", q2], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    say(f"   cases,policies(2 valid + 1 refused),approvals,cases-with-duplicate-policy,undelivered-events = {res}")
    cs, pol, apr, dup, undel = (int(x) for x in res.split("|"))
    check("retries created no duplicate cases/policies/approvals; outbox fully drained", (cs, pol, apr, dup, undel) == (6, 3, 2, 0, 0), res)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    {"--expiry": expiry, "--outbox": outbox, "--slack-ping": slack_ping, "--happy": happy, "--healthy": healthy}.get(mode, main)()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (ROOT / "proof").mkdir(exist_ok=True)
    (ROOT / "proof" / f"e2e{mode.replace('--', '-')}-{stamp}.log").write_text("\n".join(LOG) + "\n")
    say(f"\nRESULT: {'ALL CHECKS PASSED' if not FAILS else 'FAILED: ' + '; '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)
