#!/usr/bin/env python3
"""Generates the three n8n workflow JSON files (n8n 1.123.84 parameter names). Run: python3 n8n/build_workflows.py

n8n only SCHEDULES, ROUTES, WAITS and CALLS BACK. It never decides what is waste and never touches a cloud API.
Secrets are read from n8n's environment ($env.*), not stored in the workflow files.
"""
import json
import pathlib

OUT = pathlib.Path(__file__).parent / "workflows"
OUT.mkdir(exist_ok=True)
API = "{{ $env.SENTINEL_API }}"
AUTH = [{"name": "Authorization", "value": "=Bearer {{ $env.SENTINEL_N8N_TOKEN }}"}]


class WF:
    def __init__(self, wid, name):
        self.id, self.name, self.nodes, self.conn, self.x = wid, name, [], {}, 0

    def add(self, name, type_, ver, params, pos, **extra):
        self.nodes.append({"id": f"{self.id}-{len(self.nodes)}", "name": name, "type": type_, "typeVersion": ver,
                           "position": pos, "parameters": params, **extra})
        return name

    def link(self, a, b, out=0):
        outs = self.conn.setdefault(a, {"main": []})["main"]
        while len(outs) <= out:
            outs.append([])
        outs[out].append({"node": b, "type": "main", "index": 0})

    def save(self):
        doc = {"id": self.id, "name": self.name, "nodes": self.nodes, "connections": self.conn, "active": False,
               "settings": {"executionOrder": "v1"}, "pinData": {}, "versionId": self.id + "-v1"}
        (OUT / f"{self.name.lower().replace(' ', '-')}.json").write_text(json.dumps(doc, indent=2) + "\n")


def http(method, url, headers=None, body=None, raw=False, timeout=60000, full=True):
    p = {"method": method, "url": url, "sendHeaders": True, "specifyHeaders": "keypair",
         "headerParameters": {"parameters": headers if headers is not None else AUTH},
         "options": {"timeout": timeout, "response": {"response": {"fullResponse": full, "neverError": True}}}}
    if body is not None:
        p["sendBody"] = True
        if raw:
            p.update({"contentType": "raw", "rawContentType": "application/json", "body": body})
        else:
            p.update({"contentType": "json", "specifyBody": "json", "jsonBody": body})
    return p


def if_true(expr):
    return {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
                           "conditions": [{"id": "c1", "leftValue": expr, "rightValue": "", "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
                           "combinator": "and"}, "options": {}}


def respond(code, body_expr):
    return {"respondWith": "json", "responseBody": body_expr, "options": {"responseCode": code}}


def webhook(path):
    return {"httpMethod": "POST", "path": path, "responseMode": "responseNode", "options": {"rawBody": True}}


# ------------------------------------------------------------------ shared Code snippets
VERIFY = r"""
// Verify HMAC over the RAW body + timestamp window, then dedupe by event_id. Never throws: returns a verdict.
const crypto = require('crypto');
const it = $input.first();
const h = it.json.headers || {};
const out = (o) => [{ json: o }];
const b64 = it.binary && it.binary.data ? it.binary.data.data : null;
if (!b64) return out({ valid: false, reason: 'no raw body' });
const raw = Buffer.from(b64, 'base64');
const ts = h['x-sentinel-timestamp'];
const sig = h['x-sentinel-signature'] || '';
if (!ts || !(Math.abs(Date.now() / 1000 - Number(ts)) <= 300)) return out({ valid: false, reason: 'stale or missing timestamp' });
const want = crypto.createHmac('sha256', $env.SENTINEL_HMAC_SECRET).update(ts + '.').update(raw).digest('hex');
const a = Buffer.from(want), b = Buffer.from(sig);
if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return out({ valid: false, reason: 'bad signature' });
const event = JSON.parse(raw.toString('utf8'));
const seen = $getWorkflowStaticData('global').seen || {};
return out({ valid: true, duplicate: !!seen[event.event_id], event });
"""

MARK_SEEN = r"""
// Record the event as handled only AFTER success, so a failed delivery is retried by the outbox, not swallowed.
const sd = $getWorkflowStaticData('global');
sd.seen = sd.seen || {};
const ev = $('Verify signature').first().json.event;
sd.seen[ev.event_id] = Date.now();
const ks = Object.keys(sd.seen);
if (ks.length > 500) ks.sort((x, y) => sd.seen[x] - sd.seen[y]).slice(0, ks.length - 500).forEach((k) => delete sd.seen[k]);
return [{ json: { handled: true, event_id: ev.event_id, ...( $input.first() ? { upstream: $input.first().json.statusCode ?? null } : {}) } }];
"""


def build_analysis():
    w = WF("sentinelAnalysis01", "Sentinel scheduled analysis")
    sched = w.add("Hourly schedule", "n8n-nodes-base.scheduleTrigger", 1.2, {"rule": {"interval": [{"field": "hours", "hoursInterval": 1}]}}, [0, 0])
    hook = w.add("Run now (manual)", "n8n-nodes-base.webhook", 2, webhook("sentinel-run-now"), [0, 200], webhookId="sentinel-run-now")
    ok = w.add("Secret ok?", "n8n-nodes-base.if", 2.2, if_true("={{ $json.headers['x-run-now-secret'] === $env.SENTINEL_RUN_NOW_SECRET }}"), [220, 200])
    r202 = w.add("Respond 202", "n8n-nodes-base.respondToWebhook", 1.1, respond(202, '={{ JSON.stringify({ started: true }) }}'), [440, 120])
    r401 = w.add("Respond 401", "n8n-nodes-base.respondToWebhook", 1.1, respond(401, '={{ JSON.stringify({ error: "bad run-now secret" }) }}'), [440, 300])
    sync = w.add("CloudQuery sync", "n8n-nodes-base.httpRequest", 4.2,
                 http("POST", "={{ $env.SENTINEL_CQ_URL }}/sync", [{"name": "Authorization", "value": "=Bearer {{ $env.SENTINEL_CQ_TOKEN }}"}], timeout=600000, full=False), [680, 0])
    build = w.add("Build analysis request", "n8n-nodes-base.code", 2, {"jsCode": r"""
// The CloudQuery job returns a completion record; carry its sync_id into the analysis (snapshot lineage).
const s = $input.first().json;
return [{ json: { tenant_ids: ['payments', 'analytics'], idempotency_prefix: 'run-' + s.sync_id,
  sync: { sync_id: s.sync_id, started_at: s.started_at, finished_at: s.finished_at, plugin_versions: s.plugin_versions, exit_code: s.exit_code } } }];
"""}, [900, 0])
    ana = w.add("Analyze (API)", "n8n-nodes-base.httpRequest", 4.2, http("POST", f"={API}/internal/analyze", body="={{ JSON.stringify($json) }}", timeout=300000), [1120, 0])
    w.link(sched, sync); w.link(hook, ok); w.link(ok, r202, 0); w.link(ok, r401, 1); w.link(r202, sync)
    w.link(sync, build); w.link(build, ana)
    w.save()


def build_intake():
    w = WF("sentinelIntake0001", "Sentinel case intake")
    hook = w.add("Case event", "n8n-nodes-base.webhook", 2, webhook("sentinel-case"), [0, 0], webhookId="sentinel-case")
    ver = w.add("Verify signature", "n8n-nodes-base.code", 2, {"jsCode": VERIFY}, [200, 0])
    valid = w.add("Valid?", "n8n-nodes-base.if", 2.2, if_true("={{ $json.valid }}"), [400, 0])
    r401 = w.add("Respond 401", "n8n-nodes-base.respondToWebhook", 1.1, respond(401, '={{ JSON.stringify({ error: $json.reason }) }}'), [600, 140])
    dup = w.add("Duplicate?", "n8n-nodes-base.if", 2.2, if_true("={{ $json.duplicate }}"), [600, -60])
    rdup = w.add("Respond duplicate", "n8n-nodes-base.respondToWebhook", 1.1, respond(200, '={{ JSON.stringify({ duplicate: true }) }}'), [800, -140])
    case = w.add("Fetch case", "n8n-nodes-base.httpRequest", 4.2, http("GET", f"={API}/cases/{{{{ $node['Verify signature'].json.event.case_id }}}}", full=False), [800, 40])
    route = w.add("Route = dry_run?", "n8n-nodes-base.if", 2.2,
                  if_true("={{ $json.content.recommendation.route === 'dry_run' && $json.state === 'INVESTIGATED' }}"), [1000, 40])
    dry = w.add("Start dry-run (API)", "n8n-nodes-base.httpRequest", 4.2,
                http("POST", f"={API}/cases/{{{{ $node['Verify signature'].json.event.case_id }}}}/dry-run"), [1200, -20])
    fin = w.add("Finish", "n8n-nodes-base.code", 2, {"jsCode": r"""
const r = $input.first().json;
if (r.statusCode >= 500) throw new Error('dry-run failed with ' + r.statusCode + ': the outbox will retry this event');
""" + MARK_SEEN}, [1400, -20])
    digest = w.add("Digest only (no action)", "n8n-nodes-base.code", 2, {"jsCode": MARK_SEEN}, [1200, 120])
    ok = w.add("Respond 200", "n8n-nodes-base.respondToWebhook", 1.1, respond(200, '={{ JSON.stringify({ ok: true, handled: $json.handled }) }}'), [1600, 40])
    w.link(hook, ver); w.link(ver, valid); w.link(valid, dup, 0); w.link(valid, r401, 1)
    w.link(dup, rdup, 0); w.link(dup, case, 1); w.link(case, route); w.link(route, dry, 0); w.link(route, digest, 1)
    w.link(dry, fin); w.link(fin, ok); w.link(digest, ok)
    w.save()


def build_approval():
    w = WF("sentinelApproval001", "Sentinel approval")
    hook = w.add("Approval requested", "n8n-nodes-base.webhook", 2, webhook("sentinel-approval"), [0, 0], webhookId="sentinel-approval")
    ver = w.add("Verify signature", "n8n-nodes-base.code", 2, {"jsCode": VERIFY}, [200, 0])
    valid = w.add("Valid?", "n8n-nodes-base.if", 2.2, if_true("={{ $json.valid }}"), [400, 0])
    r401 = w.add("Respond 401", "n8n-nodes-base.respondToWebhook", 1.1, respond(401, '={{ JSON.stringify({ error: $json.reason }) }}'), [600, 140])
    dup = w.add("Duplicate?", "n8n-nodes-base.if", 2.2, if_true("={{ $json.duplicate }}"), [600, -60])
    rdup = w.add("Respond duplicate", "n8n-nodes-base.respondToWebhook", 1.1, respond(200, '={{ JSON.stringify({ duplicate: true }) }}'), [800, -140])
    seen = w.add("Mark seen", "n8n-nodes-base.code", 2, {"jsCode": MARK_SEEN}, [800, 40])
    acc = w.add("Respond accepted", "n8n-nodes-base.respondToWebhook", 1.1, respond(200, '={{ JSON.stringify({ accepted: true }) }}'), [1000, 40])
    pkt = w.add("Get approval packet", "n8n-nodes-base.httpRequest", 4.2,
                http("GET", f"={API}/approvals/{{{{ $node['Verify signature'].json.event.approval_id }}}}", full=False), [1200, 40])
    pub = w.add("Publish form URL", "n8n-nodes-base.httpRequest", 4.2,
                http("POST", f"={API}/approvals/{{{{ $json.approval_id }}}}/form-url", body="={{ JSON.stringify({ form_url: $execution.resumeFormUrl }) }}", full=False), [1400, 40])
    form = w.add("Build form", "n8n-nodes-base.code", 2, {"jsCode": r"""
// Everything shown comes from the API's own record of the approval; values are HTML-escaped.
const p = $('Get approval packet').first().json;
const e = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const err = $json.lastError ? `<p style="color:#b3261e"><b>Previous attempt refused:</b> ${e($json.lastError)}</p>` : '';
const rows = [
  ['Tenant', p.tenant_id], ['Accountable owner', `${p.owner} (confidence ${p.attribution_confidence})`], ['Action', `${p.action} via template ${p.template}`],
  ['Exact targets (' + p.target_count + ')', p.targets.map((t) => `${t.id}${t.name ? ' (' + t.name + ')' : ''}`).join(', ')],
  ['Excluded', (p.excluded || []).map((x) => `${x.resource_id}: ${x.reason}`).join('; ') || 'none'],
  ['Projected saving', `${p.projected_savings.monthly} ${p.projected_savings.currency}/month (projected, not realized)`],
  ['Assumptions', (p.projected_savings.assumptions || []).join('; ')], ['Risk', p.risk], ['Rollback', p.rollback], ['Why', (p.why || []).join('; ')],
  ['Policy sha256', p.policy_sha256], ['Target-set sha256', p.target_set_sha256], ['Case / version', `${p.case_id} / v${p.case_version}`], ['Expires (UTC)', p.expires_at],
];
const html = err + '<table style="text-align:left;font-size:13px">' + rows.map(([k, v]) => `<tr><th style="padding-right:12px;vertical-align:top">${e(k)}</th><td style="word-break:break-all">${e(v)}</td></tr>`).join('') + '</table>' +
  '<p>Your decision is checked by the Sentinel API against its own copy of these values. The form cannot change targets, policy or tenant.</p>';
return [{ json: { formHtml: html } }];
"""}, [1600, 40])
    wait = w.add("Wait for approver", "n8n-nodes-base.wait", 1.1, {
        "resume": "form", "formTitle": "Sentinel approval", "formDescription": "={{ $json.formHtml }}",
        "formFields": {"values": [
            {"fieldLabel": "Approver ID", "fieldType": "text", "requiredField": True, "placeholder": "e.g. payments-approver"},
            {"fieldLabel": "Approver PIN", "fieldType": "password", "requiredField": True},
            {"fieldLabel": "Decision", "fieldType": "dropdown", "requiredField": True, "fieldOptions": {"values": [{"option": "approve"}, {"option": "reject"}]}},
            {"fieldLabel": "Note", "fieldType": "textarea"}]},
        "limitWaitTime": True, "limitType": "atSpecifiedTime",
        "maxDateAndTime": "={{ new Date(new Date($node['Get approval packet'].json.expires_at).getTime() + 3000).toISOString() }}",
        "options": {}}, [1800, 40], webhookId="sentinel-approval-form")
    cbk = w.add("Build signed callback", "n8n-nodes-base.code", 2, {"jsCode": r"""
// Signs EXACTLY the bytes that the next node sends. The body carries only identifiers and the values the approver saw.
const crypto = require('crypto');
const p = $('Get approval packet').first().json;
const f = $input.first().json;
const decided = !!f['Decision'];                       // a timed-out wait resumes with no form data
const body = {
  approval_id: p.approval_id, case_id: p.case_id, decision: decided ? f['Decision'] : 'timeout',
  actor_id: decided ? String(f['Approver ID']).trim() : 'system:n8n-timeout', actor_proof: decided ? String(f['Approver PIN']) : null,
  case_version: p.case_version, policy_sha256: p.policy_sha256, target_set_sha256: p.target_set_sha256, expires_at: p.expires_at,
  idempotency_key: [p.approval_id, decided ? f['Decision'] : 'timeout', decided ? String(f['Approver ID']).trim() : 'timeout'].join(':'),
};
const raw = JSON.stringify(body);
const ts = String(Math.floor(Date.now() / 1000));
const sig = crypto.createHmac('sha256', $env.SENTINEL_HMAC_SECRET).update(ts + '.').update(raw).digest('hex');
return [{ json: { raw, ts, sig } }];
"""}, [2000, 40])
    post = w.add("Callback /approve (API)", "n8n-nodes-base.httpRequest", 4.2,
                 http("POST", f"={API}/approve", headers=AUTH + [{"name": "x-sentinel-timestamp", "value": "={{ $json.ts }}"}, {"name": "x-sentinel-signature", "value": "={{ $json.sig }}"}],
                      body="={{ $json.raw }}", raw=True), [2200, 40])
    retry = w.add("Refused (identity)?", "n8n-nodes-base.if", 2.2, if_true("={{ $json.statusCode === 403 }}"), [2400, 40])
    seterr = w.add("Remember refusal", "n8n-nodes-base.code", 2, {"jsCode": "return [{ json: { lastError: ($input.first().json.body && $input.first().json.body.error) || 'refused' } }];"}, [2600, 0])
    w.link(hook, ver); w.link(ver, valid); w.link(valid, dup, 0); w.link(valid, r401, 1)
    w.link(dup, rdup, 0); w.link(dup, seen, 1); w.link(seen, acc); w.link(acc, pkt); w.link(pkt, pub); w.link(pub, form)
    w.link(form, wait); w.link(wait, cbk); w.link(cbk, post); w.link(post, retry); w.link(retry, seterr, 0); w.link(seterr, form)
    w.save()


if __name__ == "__main__":
    build_analysis(); build_intake(); build_approval()
    print("wrote", sorted(p.name for p in OUT.glob("*.json")))
