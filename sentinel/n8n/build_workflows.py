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
// Verify HMAC over the RAW body + timestamp window. Never throws: returns a verdict.
const crypto = require('crypto');
const it = $input.first();
const h = it.json.headers || {};
const out = (o) => [{ json: o }];
let raw = null;
if (it.binary && it.binary.data) raw = await this.helpers.getBinaryDataBuffer(0, 'data');   // works in memory and filesystem binary modes
if (!raw) return out({ valid: false, reason: 'no raw body' });
const ts = h['x-sentinel-timestamp'];
const sig = h['x-sentinel-signature'] || '';
if (!ts || !(Math.abs(Date.now() / 1000 - Number(ts)) <= 300)) return out({ valid: false, reason: 'stale or missing timestamp' });
const want = crypto.createHmac('sha256', $env.SENTINEL_HMAC_SECRET).update(ts + '.').update(raw).digest('hex');
const a = Buffer.from(want), b = Buffer.from(sig);
if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return out({ valid: false, reason: 'bad signature' });
const event = JSON.parse(raw.toString('utf8'));
// Identifiers become URL path segments: accept only the exact formats the API generates (defense in depth; the event is already HMAC-signed).
const malformed = (k, re) => event[k] !== undefined && !re.test(String(event[k]));
if (malformed('event_id', /^evt_[A-Za-z0-9_]{1,64}$/) || malformed('approval_id', /^apr_[a-f0-9]{8,32}$/) || malformed('case_id', /^case_[A-Za-z0-9_-]{1,120}$/))
  return out({ valid: false, reason: 'malformed identifier' });
return out({ valid: true, duplicate: false, event });   // dedupe is checked against the API, not n8n static data
"""

# n8n answers a webhook 200/empty when a node fails after the trigger, and the API outbox treats 2xx as "delivered".
# So every call that must succeed has an ERROR OUTPUT that answers an explicit HTTP 500 (-> outbox retries, event NOT marked seen).
CHECK_200 = r"""
const r = $input.first().json;
if (r.statusCode !== 200) throw new Error('API answered HTTP ' + r.statusCode);
return [{ json: JSON.parse(JSON.stringify(r.body)) }];     // plain copy: returning n8n proxy objects overflows the stack
"""
CHECK_DRYRUN = r"""
const r = $input.first().json;
if (r.statusCode >= 500) throw new Error('dry-run failed with HTTP ' + r.statusCode);
// 2xx = done; 4xx (e.g. 409 blocked / not eligible) is a definite answer from the API, not something a retry fixes
return [{ json: { dry_run_status: r.statusCode, body: JSON.parse(JSON.stringify(r.body ?? null)) } }];
"""
ON_ERR = {"onError": "continueErrorOutput"}


def r500(w, pos):
    return w.add("Respond 500 (outbox will retry)", "n8n-nodes-base.respondToWebhook", 1.1, respond(500, '={{ JSON.stringify({ error: "temporary failure, retry" }) }}'), pos)



def seen_nodes(w, workflow, pos_q, fail):
    """Duplicate check against the API (race-free, survives n8n restarts). Returns (first_node, last_node)."""
    q = w.add("Seen before?", "n8n-nodes-base.httpRequest", 4.2, http("GET", f"={API}/internal/events/{{{{ encodeURIComponent($json.event.event_id) }}}}"), pos_q, **ON_ERR)
    c = w.add("Check seen", "n8n-nodes-base.code", 2, {"jsCode": r"""
const r = $input.first().json;
if (r.statusCode !== 200) throw new Error('dedupe lookup answered HTTP ' + r.statusCode);
return [{ json: { duplicate: !!(r.body && r.body.seen) } }];
"""}, [pos_q[0] + 150, pos_q[1]], **ON_ERR)
    w.link(q, c, 0); w.link(q, fail, 1); w.link(c, fail, 1)
    return q, c


def mark_nodes(w, workflow, pos, fail):
    """Record the event as handled ONLY after success, via the API. Returns (first_node, last_node)."""
    m = w.add("Mark seen (API)", "n8n-nodes-base.httpRequest", 4.2,
              {**http("PUT", f"={API}/internal/events/{{{{ encodeURIComponent($node['Verify signature'].json.event.event_id) }}}}", body=f"={{{{ JSON.stringify({{ workflow: '{workflow}' }}) }}}}")}, pos, **ON_ERR)
    k = w.add("Mark handled", "n8n-nodes-base.code", 2, {"jsCode": r"""
const r = $input.first().json;
if (r.statusCode !== 200) throw new Error('marking the event seen answered HTTP ' + r.statusCode);
return [{ json: { handled: true } }];
"""}, [pos[0] + 150, pos[1]], **ON_ERR)
    w.link(m, k, 0); w.link(m, fail, 1); w.link(k, fail, 1)
    return m, k



def summary_node(w, case_id_expr, pos):
    """Best-effort call to the API for a fact-checked investigation summary. A failure here must never stop a notification."""
    return w.add("Summarize case", "n8n-nodes-base.httpRequest", 4.2,
                 http("GET", f"={API}/cases/{{{{ encodeURIComponent({case_id_expr}) }}}}/summary", timeout=30000), pos, onError="continueRegularOutput")


SM_JS = r"""
const sm = (() => { try { const b = $('Summarize case').first().json.body; return b && b.summary ? b : null; } catch (e) { return null; } })();
const brief = sm ? ['*In short* _(' + (sm.source === 'llm' ? 'AI-written, numbers fact-checked' : 'auto-generated from the data') + ')_', esc(sm.summary), ''] : [];
"""

SUMMARY_JS = r"""
try { lines.push('', '_ref ' + $('Verify signature').first().json.event.event_id + '_'); } catch (e) { /* no reference available */ }
"""

SLACK_ON = "={{ !!$env.SENTINEL_SLACK_WEBHOOK_URL && !!$json.slack_text }}"
CHECK_SLACK = r"""
// Slack incoming webhooks answer 200 "ok". Temporary trouble (429/5xx/network) is retried; a 4xx is permanent
// (bad URL / revoked webhook): it is surfaced in the execution but never retried forever.
const r = $input.first().json; const c = Number(r.statusCode || 0);
if (c === 0 || c === 429 || c >= 500) throw new Error('Slack temporary failure HTTP ' + c);
return [{ json: { slack_status: c, slack_ok: c === 200, slack_error: c === 200 ? null : String(r.body || '').slice(0, 120) } }];
"""
ESC = ("const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');\n"
       "const money = (x) => '$' + Number(x).toFixed(2).replace(/\\B(?=(\\d{3})+(?!\\d))/g, ',');\n"
       "const when = (s) => { try { const d = new Date(s).toISOString(); return d.slice(0, 10) + ' ' + d.slice(11, 16) + ' UTC'; } catch (e) { return esc(s); } };")


def slack_chain(w, tag, src, dst, pos, fail=None):
    """src (json.slack_text) -> [Slack on?] -> POST webhook -> check -> dst.  Silent when SENTINEL_SLACK_WEBHOOK_URL is unset.
    fail given  -> blocking (error output answers HTTP 500 so the outbox retries).
    fail None   -> best effort: a Slack problem can never stop an approval."""
    extra = ON_ERR if fail else {"onError": "continueRegularOutput"}
    on = w.add(f"{tag}: Slack on?", "n8n-nodes-base.if", 2.2, if_true(SLACK_ON), pos)
    post = w.add(f"{tag}: Post to Slack", "n8n-nodes-base.httpRequest", 4.2,
                 http("POST", "={{ $env.SENTINEL_SLACK_WEBHOOK_URL }}", [{"name": "Content-Type", "value": "application/json"}],
                      body="={{ JSON.stringify({ text: $json.slack_text }) }}", timeout=20000), [pos[0] + 200, pos[1] - 60], retryOnFail=True, maxTries=3, waitBetweenTries=2000, **extra)
    chk = w.add(f"{tag}: Check Slack", "n8n-nodes-base.code", 2, {"jsCode": CHECK_SLACK}, [pos[0] + 400, pos[1] - 60], **extra)
    w.link(src, on); w.link(on, post, 0); w.link(post, chk, 0)
    if dst:
        w.link(on, dst, 1); w.link(chk, dst, 0)
    if fail:
        w.link(post, fail, 1); w.link(chk, fail, 1)
    return on


def build_analysis():
    w = WF("sentinelAnalysis01", "Sentinel scheduled analysis")
    sched = w.add("Hourly schedule", "n8n-nodes-base.scheduleTrigger", 1.2, {"rule": {"interval": [{"field": "hours", "hoursInterval": 1}]}}, [0, 0])
    hook = w.add("Run now (manual)", "n8n-nodes-base.webhook", 2, webhook("sentinel-run-now"), [0, 200], webhookId="sentinel-run-now")
    ok = w.add("Secret ok?", "n8n-nodes-base.if", 2.2, if_true("={{ !!$env.SENTINEL_RUN_NOW_SECRET && $json.headers['x-run-now-secret'] === $env.SENTINEL_RUN_NOW_SECRET }}"), [220, 200])
    r202 = w.add("Respond 200 (started)", "n8n-nodes-base.respondToWebhook", 1.1, respond(200, '={{ JSON.stringify({ started: true }) }}'), [440, 120])
    r401 = w.add("Respond 401", "n8n-nodes-base.respondToWebhook", 1.1, respond(401, '={{ JSON.stringify({ error: "bad run-now secret" }) }}'), [440, 300])
    sync = w.add("CloudQuery sync", "n8n-nodes-base.httpRequest", 4.2,
                 http("POST", "={{ $env.SENTINEL_CQ_URL }}/sync", [{"name": "Authorization", "value": "=Bearer {{ $env.SENTINEL_CQ_TOKEN }}"}], timeout=600000, full=False), [680, 0])
    build = w.add("Build analysis request", "n8n-nodes-base.code", 2, {"jsCode": r"""
// A failed or incomplete sync PAUSES analysis: fail the run loudly (a stale snapshot may explain a case, never authorize one).
const s = $input.first().json || {};
if (!s.sync_id) throw new Error('sync returned no sync_id - analysis paused');
if (Number(s.exit_code) !== 0) throw new Error('sync exit_code=' + s.exit_code + ' - analysis paused');
if (!s.finished_at) throw new Error('sync has no finished_at - analysis paused');
return [{ json: { tenant_ids: ['payments', 'analytics'], idempotency_prefix: 'run-' + s.sync_id,
  sync: { sync_id: s.sync_id, started_at: s.started_at, finished_at: s.finished_at, plugin_versions: s.plugin_versions, exit_code: s.exit_code } } }];
"""}, [900, 0])
    ana = w.add("Analyze (API)", "n8n-nodes-base.httpRequest", 4.2, http("POST", f"={API}/internal/analyze", body="={{ JSON.stringify($json) }}", timeout=300000, full=False), [1120, 0])
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
    fail = r500(w, [1400, 300])
    case = w.add("Fetch case", "n8n-nodes-base.httpRequest", 4.2, http("GET", f"={API}/cases/{{{{ encodeURIComponent($node['Verify signature'].json.event.case_id) }}}}"), [800, 40], **ON_ERR)
    chk = w.add("Check case", "n8n-nodes-base.code", 2, {"jsCode": CHECK_200}, [1000, 40], **ON_ERR)
    route = w.add("Route = dry_run?", "n8n-nodes-base.if", 2.2,
                  if_true("={{ $json.content.recommendation.route === 'dry_run' && $json.state === 'INVESTIGATED' }}"), [1200, 40])
    dry = w.add("Start dry-run (API)", "n8n-nodes-base.httpRequest", 4.2,
                http("POST", f"={API}/cases/{{{{ encodeURIComponent($node['Verify signature'].json.event.case_id) }}}}/dry-run"), [1400, -20], **ON_ERR)
    chkd = w.add("Check dry-run", "n8n-nodes-base.code", 2, {"jsCode": CHECK_DRYRUN}, [1600, -20], **ON_ERR)
    sq, sc = seen_nodes(w, "intake", [500, -140], fail)
    seen, seen_done = mark_nodes(w, "intake", [1800, 40], fail)
    ok = w.add("Respond 200", "n8n-nodes-base.respondToWebhook", 1.1, respond(200, '={{ JSON.stringify({ ok: true, handled: $json.handled }) }}'), [2200, 40])
    w.link(hook, ver); w.link(ver, valid); w.link(valid, sq, 0); w.link(valid, r401, 1); w.link(sc, dup)
    w.link(dup, rdup, 0); w.link(dup, case, 1)
    w.link(case, chk, 0); w.link(case, fail, 1); w.link(chk, route, 0); w.link(chk, fail, 1)
    dig = w.add("Build digest message", "n8n-nodes-base.code", 2, {"jsCode": ESC + SM_JS + r"""
// Only cases that need a human produce a Slack message; dismissed/legitimate ones stay silent (digest only, no action).
const k = $('Check case').first().json;
const rec = k.content.recommendation;
if (rec.route !== 'review') return [{ json: { slack_text: '' } }];
const a = k.content.attribution;
const lines = [`:mag: *Needs human review* \u2014 ${esc(k.content.workload)} \u00b7 ${esc(k.tenant_id)}`,
  `\`${esc(k.case_id)}\` \u00b7 owner: ${esc(a.owner || 'unknown')}`,
  '_Alert only: the system changes nothing. A person reviews this one; there is no approval form._', '', ...brief];
if (sm && sm.briefing) lines.push(sm.briefing);
else lines.push('why: ' + k.content.assessment.reasons.map(esc).join('; '), `estimated cost: ${money(rec.projected_savings.monthly)}/month (estimate, not measured)`);
""" + SUMMARY_JS + r"""return [{ json: { slack_text: lines.join('\n') } }];
"""}, [1400, 140])
    needs = w.add("Needs review?", "n8n-nodes-base.if", 2.2, if_true("={{ $json.content.recommendation.route === 'review' }}"), [1300, 140])
    summ = summary_node(w, "$node['Verify signature'].json.event.case_id", [1400, 220])
    w.link(route, dry, 0); w.link(route, needs, 1)         # not a dry-run case -> digest (+ Slack only if it needs review)
    w.link(needs, summ, 0); w.link(needs, seen, 1); w.link(summ, dig)
    slack_chain(w, "Review", dig, seen, [1500, 200], fail)
    w.link(dry, chkd, 0); w.link(dry, fail, 1); w.link(chkd, seen, 0); w.link(chkd, fail, 1)
    w.link(seen_done, ok)
    w.save()


def build_approval():
    w = WF("sentinelApproval001", "Sentinel approval")
    hook = w.add("Approval requested", "n8n-nodes-base.webhook", 2, webhook("sentinel-approval"), [0, 0], webhookId="sentinel-approval")
    ver = w.add("Verify signature", "n8n-nodes-base.code", 2, {"jsCode": VERIFY}, [200, 0])
    valid = w.add("Valid?", "n8n-nodes-base.if", 2.2, if_true("={{ $json.valid }}"), [400, 0])
    r401 = w.add("Respond 401", "n8n-nodes-base.respondToWebhook", 1.1, respond(401, '={{ JSON.stringify({ error: $json.reason }) }}'), [600, 140])
    dup = w.add("Duplicate?", "n8n-nodes-base.if", 2.2, if_true("={{ $json.duplicate }}"), [600, -60])
    rdup = w.add("Respond duplicate", "n8n-nodes-base.respondToWebhook", 1.1, respond(200, '={{ JSON.stringify({ duplicate: true }) }}'), [800, -140])
    fail = r500(w, [1500, 300])
    pkt = w.add("Get approval packet", "n8n-nodes-base.httpRequest", 4.2,
                http("GET", f"={API}/approvals/{{{{ encodeURIComponent($node['Verify signature'].json.event.approval_id) }}}}"), [800, 40], **ON_ERR)
    chkp = w.add("Check packet", "n8n-nodes-base.code", 2, {"jsCode": CHECK_200}, [1000, 40], **ON_ERR)
    pub = w.add("Publish form URL", "n8n-nodes-base.httpRequest", 4.2,
                http("POST", f"={API}/approvals/{{{{ encodeURIComponent($node['Check packet'].json.approval_id) }}}}/form-url", body="={{ JSON.stringify({ form_url: $execution.resumeFormUrl }) }}"), [1200, 40], **ON_ERR)
    chkpub = w.add("Check published", "n8n-nodes-base.code", 2, {"jsCode": r"""
const r = $input.first().json;
if (r.statusCode !== 200) throw new Error('form-url publish answered HTTP ' + r.statusCode);
return [{ json: { published: true } }];
"""}, [1400, 40], **ON_ERR)
    sq, sc = seen_nodes(w, "approval", [500, -140], fail)
    seen, seen_done = mark_nodes(w, "approval", [1600, 40], fail)
    acc = w.add("Respond accepted", "n8n-nodes-base.respondToWebhook", 1.1, respond(200, '={{ JSON.stringify({ accepted: true }) }}'), [1800, 40])
    form = w.add("Build form", "n8n-nodes-base.code", 2, {"jsCode": r"""
// Everything shown comes from the API's own record of the approval; values are HTML-escaped.
const p = $('Check packet').first().json;
const e = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const err = $json.lastError ? `<p style="color:#b3261e"><b>Previous attempt refused:</b> ${e($json.lastError)}</p>` : '';
const money = (x) => '$' + Number(x).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
const when = (t) => { try { const d = new Date(t).toISOString(); return d.slice(0, 10) + ' ' + d.slice(11, 16) + ' UTC'; } catch (x) { return t; } };
const tag = p.action_mode === 'tag_after_approval';
const targets = p.targets.map((t) => `<li><code>${e(t.id)}</code>${t.name ? ' — ' + e(t.name) : ''}</li>`).join('');
const excluded = (p.excluded || []).map((x) => `<li><code>${e(x.resource_id)}</code> — ${e(x.reason)}</li>`).join('');
const h = (t) => `<div style="margin:18px 0 6px;font-weight:700;font-size:14px;text-transform:uppercase;letter-spacing:.04em;color:#555">${e(t)}</div>`;
const kv = (k, v) => `<div style="display:flex;gap:12px;padding:5px 0;border-bottom:1px solid #eee"><div style="width:130px;flex:none;color:#666">${e(k)}</div><div style="flex:1;font-weight:600">${v}</div></div>`;
const html = `<div style="text-align:left;font-size:14px;line-height:1.5">` + err +
  `<div style="background:#fff4e5;border:1px solid #f0c27b;border-radius:6px;padding:8px 10px;margin-bottom:8px">` +
  (tag ? `Alert only. Approving lets the isolated runner add ONE reversible tag. Nothing else is changed.`
       : `Alert only. Approving only <b>records your decision</b>. The system changes nothing; the owner fixes it by hand.`) + `</div>` +
  h('Summary') + kv('Tenant', e(p.tenant_id)) + kv('Owner', e(p.owner || 'not established')) +
  kv('Estimated cost', e(money(p.projected_savings.monthly)) + ' / month <span style="font-weight:400;color:#666">(estimate, not measured)</span>') +
  kv('Risk', e(p.risk)) + kv('Expires', e(when(p.expires_at)) + ' <span style="font-weight:400;color:#666">(no answer = expires; never auto-approved)</span>') +
  h('Why it was flagged') + `<ul style="margin:4px 0 0 18px;padding:0">${(p.why || []).map((x) => `<li>${e(x)}</li>`).join('')}</ul>` +
  h('What this decision covers') + `<ul style="margin:4px 0 0 18px;padding:0">${targets}</ul>` +
  (excluded ? `<div style="margin-top:6px;color:#666">Not included:</div><ul style="margin:2px 0 0 18px;padding:0">${excluded}</ul>` : '') +
  h('If you approve') + `<div>Proposed: ${e(p.action)}.${tag ? '' : ' It is <b>not</b> applied by the system.'}</div><div>Rollback: ${e(p.rollback)}</div>` +
  `<div style="margin-top:18px;padding-top:8px;border-top:1px solid #ddd;color:#888;font-size:11px;word-break:break-all">` +
  `Technical details (checked by the API, the form cannot change them): ${e(p.case_id)} v${e(p.case_version)} · template ${e(p.template)} · policy ${e(p.policy_sha256)} · targets ${e(p.target_set_sha256)}</div></div>`;
return [{ json: { formHtml: html, retry: !!$json.lastError } }];
"""}, [2000, 40])
    smsg = w.add("Build Slack approval message", "n8n-nodes-base.code", 2, {"jsCode": ESC + SM_JS + r"""
// Sent once per approval (not again after a wrong-PIN retry). The link is the n8n form; the API still checks ID + PIN.
if ($json.retry) return [{ json: { slack_text: '' } }];
const p = $('Check packet').first().json;
const ids = p.targets.map((t) => `${esc(t.id)}${t.name ? ' (' + esc(t.name) + ')' : ''}`).join(', ');
const lines = [`:bell: *Approval needed* \u2014 ${esc(p.owner)} \u00b7 ${esc(p.tenant_id)}`,
  `\`${esc(p.case_id)}\` \u00b7 risk: ${esc(p.risk)}`,
  p.action_mode === 'tag_after_approval' ? '_Alert only: nothing has been changed yet. A human decides; the only action the system can ever take is a reversible tag._' : '_Alert only: the system changes nothing. A human decides and fixes it by hand._', '', ...brief];
if (sm && sm.briefing) lines.push(sm.briefing, '');
else lines.push(`estimated saving: ${money(p.projected_savings.monthly)}/month (estimate, not measured)`, `rollback: ${esc(p.rollback)}`, '');
lines.push(`*Target${p.target_count > 1 ? 's (' + p.target_count + ')' : ''}:* ${ids}`);
if (p.excluded && p.excluded.length) lines.push('*Excluded:* ' + p.excluded.map((x) => `${esc(x.resource_id)} (${esc(x.reason)})`).join('; '));
lines.push(`*Decide:* <${$execution.resumeFormUrl}|Open the approval form> (approver ID + PIN) \u00b7 expires ${when(p.expires_at)} if nobody answers`);
""" + SUMMARY_JS + r"""return [{ json: { slack_text: lines.join('\n') } }];
"""}, [2100, 40])
    wait = w.add("Wait for approver", "n8n-nodes-base.wait", 1.1, {
        "resume": "form", "formTitle": "Sentinel approval", "formDescription": "={{ $node['Build form'].json.formHtml }}",
        "formFields": {"values": [
            {"fieldLabel": "Approver ID", "fieldType": "text", "requiredField": True, "placeholder": "e.g. payments-approver"},
            {"fieldLabel": "Approver PIN", "fieldType": "password", "requiredField": True},
            {"fieldLabel": "Decision", "fieldType": "dropdown", "requiredField": True, "fieldOptions": {"values": [{"option": "approve"}, {"option": "reject"}]}},
            {"fieldLabel": "Note", "fieldType": "textarea"}]},
        "limitWaitTime": True, "limitType": "atSpecifiedTime",
        "maxDateAndTime": "={{ new Date(new Date($node['Check packet'].json.expires_at).getTime() + 3000).toISOString() }}",
        "options": {}}, [2200, 40], webhookId="sentinel-approval-form")
    cbk = w.add("Build signed callback", "n8n-nodes-base.code", 2, {"jsCode": r"""
// Signs EXACTLY the bytes that the next node sends. The body carries only identifiers and the values the approver saw.
const crypto = require('crypto');
const p = $('Check packet').first().json;
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
return [{ json: { raw, ts, sig, decision: body.decision } }];
"""}, [2400, 40])
    post = w.add("Callback /approve (API)", "n8n-nodes-base.httpRequest", 4.2,
                 http("POST", f"={API}/approve", headers=AUTH + [{"name": "x-sentinel-timestamp", "value": "={{ $json.ts }}"}, {"name": "x-sentinel-signature", "value": "={{ $json.sig }}"}],
                      body="={{ $json.raw }}", raw=True), [2600, 40])
    retry = w.add("Refused (identity)?", "n8n-nodes-base.if", 2.2,
                  if_true("={{ $json.statusCode === 403 && $node['Build signed callback'].json.decision !== 'timeout' }}"), [2800, 40])
    seterr = w.add("Remember refusal", "n8n-nodes-base.code", 2, {"jsCode": "return [{ json: { lastError: String(($input.first().json.body && $input.first().json.body.error) || 'refused') } }];"}, [3000, 0])
    res = w.add("Check result", "n8n-nodes-base.code", 2, {"jsCode": r"""
// 2xx = recorded; 409 = gate already closed (duplicate click / expired / version changed) - nothing more to do. Anything else is a visible failure.
const r = $input.first().json;
const code = Number(r.statusCode);
const b = r.body && typeof r.body === 'object' ? r.body : {};
if ((code >= 200 && code < 300) || code === 409) return [{ json: { status_code: code, outcome: String(b.outcome || b.error || '') } }];
throw new Error('POST /approve failed with HTTP ' + code + ': ' + JSON.stringify(b).slice(0, 300));
"""}, [3000, 120])
    w.link(hook, ver); w.link(ver, valid); w.link(valid, sq, 0); w.link(valid, r401, 1); w.link(sc, dup)
    w.link(dup, rdup, 0); w.link(dup, pkt, 1)
    w.link(pkt, chkp, 0); w.link(pkt, fail, 1); w.link(chkp, fail, 1)
    w.link(pub, chkpub, 0); w.link(pub, fail, 1); w.link(chkpub, seen, 0); w.link(chkpub, fail, 1)
    summ = summary_node(w, "$node['Check packet'].json.case_id", [1900, 120])
    # the summary is generated BEFORE the form link is published, so the link goes live right before the Wait starts (no dead-link window)
    w.link(chkp, summ, 0); w.link(summ, pub)
    w.link(seen_done, acc); w.link(acc, form); w.link(form, smsg)
    slack_chain(w, "Approval", smsg, wait, [2150, 120])      # best effort; then the Wait
    w.link(wait, cbk); w.link(cbk, post); w.link(post, retry)
    w.link(retry, seterr, 0); w.link(retry, res, 1); w.link(seterr, form)
    w.save()



def build_notify():
    w = WF("sentinelNotify00001", "Sentinel notify")
    hook = w.add("State change event", "n8n-nodes-base.webhook", 2, webhook("sentinel-notify"), [0, 0], webhookId="sentinel-notify")
    ver = w.add("Verify signature", "n8n-nodes-base.code", 2, {"jsCode": VERIFY}, [200, 0])
    valid = w.add("Valid?", "n8n-nodes-base.if", 2.2, if_true("={{ $json.valid }}"), [400, 0])
    r401 = w.add("Respond 401", "n8n-nodes-base.respondToWebhook", 1.1, respond(401, '={{ JSON.stringify({ error: $json.reason }) }}'), [600, 140])
    fail = r500(w, [1800, 300])
    sq, sc = seen_nodes(w, "notify", [500, -140], fail)
    dup = w.add("Duplicate?", "n8n-nodes-base.if", 2.2, if_true("={{ $json.duplicate }}"), [900, -60])
    rdup = w.add("Respond duplicate", "n8n-nodes-base.respondToWebhook", 1.1, respond(200, '={{ JSON.stringify({ duplicate: true }) }}'), [1100, -140])
    msg = w.add("Build Slack message", "n8n-nodes-base.code", 2, {"jsCode": ESC + SM_JS + r"""
const ev = $('Verify signature').first().json.event;
const icon = { REFUSED: ':no_entry:', POLICY_INVALID: ':warning:', APPROVED: ':white_check_mark:', REJECTED: ':x:', EXPIRED: ':hourglass:',
               VERIFIED: ':white_check_mark:', VERIFICATION_FAILED: ':rotating_light:', EXECUTION_FAILED: ':rotating_light:' }[ev.to_state] || ':information_source:';
const needsAttention = ['REFUSED', 'POLICY_INVALID', 'EXPIRED', 'EXECUTION_FAILED', 'VERIFICATION_FAILED'].includes(ev.to_state);
const s = ev.summary || {}, d = ev.detail || {};
const lines = [`${icon} *${esc(ev.to_state)}* \u2014 ${esc(s.workload)} \u00b7 ${esc(ev.tenant_id)}`,
  `\`${esc(ev.case_id)}\` \u00b7 ${esc(ev.from_state)} \u2192 ${esc(ev.to_state)} \u00b7 by ${esc(ev.actor)}`,
  '_Alert only: the system informs; people decide and fix._'];
if (ev.to_state === 'APPROVED' && ev.action_mode !== 'tag_after_approval') lines.push('Decision recorded. The system made NO change; the owner fixes it by hand.');
if (s.owner) lines.push(`owner: ${esc(s.owner)} \u00b7 risk: ${esc(s.risk)} \u00b7 estimate: ${money(s.projected_monthly)}/month`);
lines.push('', ...brief);
if (d.reasons && d.reasons.length) lines.push('reasons: ' + d.reasons.map(esc).join('; '));
if (d.note) lines.push('note: ' + esc(d.note));
if (d.matched_ids && d.matched_ids.length) lines.push('targets: ' + d.matched_ids.map(esc).join(', '));
if (d.verification_ok !== undefined) lines.push('re-checked in the live resource state: ' + (d.verification_ok ? 'change observed' : 'CHANGE NOT OBSERVED'));
if (needsAttention && sm && sm.briefing) lines.push('', sm.briefing);
""" + SUMMARY_JS + r"""return [{ json: { slack_text: lines.join('\n') } }];
"""}, [1100, 40])
    seen, seen_done = mark_nodes(w, "notify", [1900, 40], fail)
    ok = w.add("Respond 200", "n8n-nodes-base.respondToWebhook", 1.1, respond(200, '={{ JSON.stringify({ ok: true, handled: $json.handled }) }}'), [2300, 40])
    w.link(hook, ver); w.link(ver, valid); w.link(valid, sq, 0); w.link(valid, r401, 1); w.link(sc, dup)
    summ = summary_node(w, "$node['Verify signature'].json.event.case_id", [1000, 120])
    # healthy-tenant digests carry their own text and have no case: they skip the case-summary call entirely
    isdig = w.add("Digest event?", "n8n-nodes-base.if", 2.2, if_true("={{ $node['Verify signature'].json.event.event_type === 'digest.healthy' }}"), [900, 60])
    dmsg = w.add("Build all-clear message", "n8n-nodes-base.code", 2, {"jsCode": r"""
const ev = $('Verify signature').first().json.event;
return [{ json: { slack_text: String(ev.text || '') + '\n\n_ref ' + ev.event_id + '_' } }];
"""}, [1100, 220])
    w.link(dup, rdup, 0); w.link(dup, isdig, 1); w.link(isdig, dmsg, 0); w.link(isdig, summ, 1); w.link(summ, msg)
    slack_chain(w, "Outcome", msg, seen, [1300, 40], fail)
    slack_chain(w, "AllClear", dmsg, seen, [1300, 220], fail)
    w.link(seen_done, ok)
    w.save()


if __name__ == "__main__":
    build_analysis(); build_intake(); build_approval(); build_notify()
    print("wrote", sorted(p.name for p in OUT.glob("*.json")))
