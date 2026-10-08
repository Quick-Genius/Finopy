#!/usr/bin/env python3
"""Audit every HTTP status code the running stack produced (stdlib only; run from sentinel/ after a demo run).

Sources: API access log + its outgoing calls (n8n webhooks, Custodian runner, the LLM), the runner and CloudQuery job access logs,
and every HTTP node result recorded by n8n (API calls, Slack). Exit code 1 if anything is not 2xx.
Run it on a stack that only did the happy path (`python3 scripts/e2e.py --happy`): deliberate negative tests produce 401/403/404/409/500 on purpose.
"""
import collections
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
rows = collections.defaultdict(collections.Counter)      # (source, request) -> {status: count}
normalize = lambda s: re.sub(r"\?.*", "", re.sub(r"(case_|apr_|evt_|vol-|i-|exe_|pol_)[A-Za-z0-9_-]+", r"\1*", s))


def logs(service):
    return subprocess.run(["docker", "compose", "logs", "--no-log-prefix", service], cwd=ROOT, capture_output=True, text=True).stdout


ACCESS = re.compile(r'"(GET|POST|PUT|DELETE) (\S+) HTTP/1\.[01]" (\d{3})')
CLIENT = re.compile(r'HTTP Request: (GET|POST|PUT) (https?://\S+) "HTTP/1\.[01] (\d{3})')
for svc, label in (("api", "API serves"), ("runner", "Custodian runner serves"), ("cq-runner", "CloudQuery job serves")):
    for m in ACCESS.finditer(logs(svc)):
        rows[(label, f"{m.group(1)} {normalize(m.group(2))}")][int(m.group(3))] += 1
for m in CLIENT.finditer(logs("api")):
    host = re.sub(r"https?://([^/]+).*", r"\1", m.group(2)); path = normalize(re.sub(r"https?://[^/]+", "", m.group(2)))
    who = {"n8n:5678": "API -> n8n", "runner:8080": "API -> runner"}.get(host, "API -> AI provider")
    rows[(who, f"{m.group(1)} {host}{path}")][int(m.group(3))] += 1

# every HTTP node n8n executed (API calls, Slack): statusCode / slack_status recorded in the execution data
ids = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "n8n", "-d", "n8n", "-At", "-c",
                      "select e.id||'|'||w.name||'|'||e.status from execution_entity e join workflow_entity w on w.id=e.\"workflowId\" order by e.id"],
                     cwd=ROOT, capture_output=True, text=True).stdout.split("\n")
exec_status = collections.Counter()
for line in ids:
    if "|" not in line:
        continue
    eid, wf, st = line.split("|"); exec_status[(wf, st)] += 1
    raw = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "n8n", "-d", "n8n", "-At", "-c",
                          f"select data from execution_data where \"executionId\"={eid}"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    try:
        arr = json.loads(raw)
    except ValueError:
        continue

    def res(i, d=0):
        v = arr[i]
        if d > 14:
            return None
        if isinstance(v, dict):
            return {k: res(int(x), d + 1) if isinstance(x, str) and x.isdigit() else x for k, x in v.items()}
        if isinstance(v, list):
            return [res(int(x), d + 1) if isinstance(x, str) and x.isdigit() else x for x in v]
        return v
    for node, runs in res(0)["resultData"]["runData"].items():
        for run in runs:
            for out in (run.get("data", {}).get("main") or []):
                for item in out or []:
                    j = item.get("json") or {}
                    code = j.get("statusCode") if isinstance(j.get("statusCode"), int) else j.get("slack_status") if isinstance(j.get("slack_status"), int) else None
                    if code is not None:
                        rows[(f"n8n ({wf})", node)][code] += 1

total, bad = collections.Counter(), []
width = max((len(f"{s}  {r}") for (s, r) in rows), default=40)
print(f"{'REQUEST':{width}}   STATUS CODES SEEN")
for (src, req), cnt in sorted(rows.items()):
    for code, n in cnt.items():
        total["2xx" if 200 <= code < 300 else f"{code // 100}xx"] += n
        if not 200 <= code < 300:
            bad.append((src, req, code, n))
    print(f"{src + '  ' + req:{width}}   " + ", ".join(f"{c} x{n}" for c, n in sorted(cnt.items())))
print("\nn8n executions:", ", ".join(f"{wf}: {st} x{n}" for (wf, st), n in sorted(exec_status.items())))
print("totals:", dict(total))
if bad:
    print("\nNOT 2xx:")
    for src, req, code, n in bad:
        print(f"  {code} x{n}  {src}  {req}")
print("\nRESULT:", "EVERY HTTP CALL WAS 2xx" if not bad and total else "NON-2xx RESPONSES FOUND" if bad else "no traffic found")
sys.exit(1 if bad or not total else 0)
