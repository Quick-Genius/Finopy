#!/usr/bin/env python3
"""One call: is every automation in the stack alive? (stdlib only; run from sentinel/)"""
import json, pathlib, subprocess, sys, urllib.request, urllib.error

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV = dict(l.split("=", 1) for l in (ROOT / ".env").read_text().splitlines() if "=" in l and not l.startswith("#"))
bad = []
API = "http://localhost:" + ENV.get("API_HOST_PORT", "8000")
N8N = "http://localhost:" + ENV.get("N8N_HOST_PORT", "5678")
LS = "http://localhost:" + ENV.get("LOCALSTACK_HOST_PORT", "4566")
DASH = "http://localhost:" + ENV.get("DASHBOARD_HOST_PORT", "3000")


def row(name, ok, detail=""):
    print(f"  [{'OK ' if ok else 'BAD'}] {name:46} {detail}")
    if not ok:
        bad.append(name)


def http(method, url, headers=None, body=None):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, method=method, headers=headers or {}), timeout=15) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return 0, str(e)


def sql(db, user, q):
    return subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", user, "-d", db, "-At", "-F", "|", "-c", q], cwd=ROOT, capture_output=True, text=True).stdout.strip()


print("API + background workers")
c, b = http("GET", API + "/health")
h = json.loads(b) if b.startswith("{") else {}
row("API / database / Custodian runner", c == 200 and h.get("database") == "ok" and h.get("runner") == "ok", f"HTTP {c}")
for n, st in (h.get("workers") or {}).items():
    row(f"worker: {n}", st == "alive", st)
row("outbox backlog (undelivered events)", (h.get("outbox_pending") or 0) < 20, str(h.get("outbox_pending")))
print("\nn8n workflows (local)")
for line in sql("n8n", "n8n", "select name||'|'||active from workflow_entity order by name").splitlines():
    name, active = line.split("|"); row(f"workflow active: {name}", active in ("t", "true"))
for path in ("sentinel-case", "sentinel-approval", "sentinel-notify"):
    code, _ = http("POST", f"{N8N}/webhook/{path}")
    row(f"webhook /{path} (unsigned must be 401)", code == 401, f"HTTP {code}")
code, _ = http("POST", N8N + "/webhook/sentinel-run-now", {"x-run-now-secret": "wrong"})
row("webhook /sentinel-run-now (wrong secret = 401)", code == 401, f"HTTP {code}")
fails = sql("n8n", "n8n", "select count(*) from execution_entity where status in ('error','crashed')")
row("n8n executions that failed", fails == "0", fails)
print("\nData plane")
r = subprocess.run(["docker", "compose", "exec", "-T", "cq-runner", "python", "-c", "import urllib.request;print(urllib.request.urlopen('http://localhost:8080/health',timeout=5).read().decode())"], cwd=ROOT, capture_output=True, text=True)
row("CloudQuery job service", '"ok"' in r.stdout)
c, _ = http("GET", LS + "/_localstack/health"); row("LocalStack (the only 'cloud')", c == 200)
c, _ = http("GET", DASH + "/"); row("dashboard", c == 200)
print("\nOptional integrations (informational)")
print(f"  Slack webhook configured: {'yes' if ENV.get('SLACK_WEBHOOK_URL', '').startswith('https://hooks.slack.com/') else 'no (notifications skipped)'}")
print(f"  LLM summaries: {h.get('llm')}")
print("\nRESULT:", "ALL AUTOMATIONS HEALTHY" if not bad else "PROBLEMS: " + "; ".join(bad))
sys.exit(1 if bad else 0)
