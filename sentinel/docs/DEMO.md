# Three-minute judge demo

Prereq: `./scripts/up.sh` (or `./scripts/reset.sh` for a known-clean state). Open: dashboard http://localhost:3000, n8n http://localhost:5678 (`demo@sentinel.local` / `Sentinel-Demo-1`), API docs http://localhost:8000/docs.
Personas (dummy): Payments approver `payments-approver` PIN `1111`; Analytics approver `analytics-approver` PIN `3333`.

**0:00 – setup (20 s).** "Two tenants share one cloud bill. Payments has a planned launch spike and forgotten dev instances plus a protected DR box; Analytics has an unattached volume. A plain alert sees noise; this decides which spend needs action."

**0:20 – trigger (25 s).** In n8n open *Sentinel scheduled analysis* → show the hourly schedule, then run the manual trigger:
`curl -X POST localhost:5678/webhook/sentinel-run-now -H "x-run-now-secret: $(grep RUN_NOW_SECRET .env | cut -d= -f2)"`.
CloudQuery syncs LocalStack into PostgreSQL; the API creates cases. Expect approval-ready cases in ~15–20 s (measured 16 s and 21 s).

**0:45 – detect and attribute (30 s).** Dashboard → sign in as *Priya (Payments analyst)*. Show: the checkout spike is **DISMISSED / legitimate** (launch event, real cost chart, expected vs actual); the idle fleet is **probable_waste** with owner `payments-platform` 0.95 and the evidence ladder (tag, deployment, naming); *shared-ingest* stays **needs_review** because its tag says `payments-risk` but the deployment record says `payments-platform`: no owner is invented.

**1:15 – prove (30 s).** Open `batch-dev`: the rendered Custodian policy (a template, not model text), Custodian 0.9.53 dry-run log, **exactly two targets**, `policy sha256` and `target-set sha256`, projected $420/month beside "Realized: not measured".

**1:45 – the refusal is the feature (25 s).** Open `dr-standby`: **REFUSED** before any approval; the protected instance was in scope, excluded by `tag:Protected: absent`, reason recorded.

**2:10 – human approval (25 s).** Click *Open approval form (n8n)*: tenant, owner, exact targets, savings + assumptions, risk, rollback, both hashes, expiry. First enter a wrong PIN → refused and audited; then `payments-approver` / `1111` → Approve.

**2:35 – a human fixes it; the system doesn't (25 s).** Default mode is alert-only: after the approval the case shows `APPROVED`, the audit shows `approval.recorded_no_action`, **no execution exists** and no instance in LocalStack is tagged or changed. Replay the same callback (`python3 scripts/e2e.py` does this) → `replayed:true`. Switch persona to *Anil (Analytics)*: the Payments case id returns 404. (Want the old executed-and-verified loop? Set `ACTION_MODE=tag_after_approval` in `.env`, then `./scripts/reset.sh`.)

Hands-free alternative: `./scripts/replay.sh` resets, runs everything above and prints every PASS/FAIL check.

## Measured (this machine, 2026-10-08, warm images)
| Measure | Result |
|---|---|
| n8n trigger → approval-ready (CloudQuery sync + analysis + dry-run + form published) | **16 s, 21 s** (target < 5 min) |
| Tenant ID on events / rows | 100 % (NOT NULL columns, RLS, event payloads) |
| Unapproved destructive actions | 0 (no destructive template exists; Custodian runs only non-destructive `mark-for-op` / `tag`) |
| Cold start (images built, volumes empty) | ~1 min; first build much longer (Go plugin compile, n8n image) |

# Honest production gaps
- **Identity:** opaque static demo tokens; `/demo/personas` hands them out when `DEMO_MODE=true`; approver proof is a PIN typed into an n8n form (stand-in for Slack identity). The n8n form URL itself is unauthenticated (`/form-waiting/<execution id>`, sequential), protected only by the PIN check in the API. No SSO, no TLS, secrets in `.env`.
- **Not independent verification:** the post-check is a read-only call through the same runner; a CloudQuery re-sync or separate read role would be stronger. LocalStack does not prove invoice savings; **realized savings are reported as "not measured"**.
- **Fixtures:** LocalStack cannot back-date launch times, so `RESOURCE_MIN_AGE_DAYS=0` in the demo (default 7); CPU metrics are seeded with `put_metric_data`; one LocalStack account, so the *account-map* attribution rung is unit-tested but not exercised live; cost facts are synthetic, not a CUR import.
- **Tenant isolation is only as good as the app + RLS defence in depth:** the API process can set `app.system` itself, so a SQL-injection bug would bypass RLS. A tenant-specific action role per runner call is not modelled (LocalStack ignores IAM).
- **Audit is append-only by privilege + trigger, not tamper-evident** (no hash chain, owner can truncate).
- **Escalation = `EXPIRED` + audit + dashboard queue**; no Slack/email notification, no two-person gate for high risk, no "ticket-only" route for destroy proposals.
- **LLM:** none is wired. The intent proposer is deterministic; `ActionIntent` is the validated entry point if one is added.
- **CloudQuery:** AWS plugin pinned at v22.19.2 (older than the hub's latest); PostgreSQL destination built from source because the hub requires a login; arm64 only was exercised. LocalStack is pinned to 4.14.0 because 2026.09.x needs an account token.
- **Operational:** workers (outbox, executor, sweeper) are threads in one API process; one runner; no rate limiting/pagination; stale `running` executions become `inconclusive` on restart and are not retried.
- **Dashboard:** builds, serves, proxies, and every data path was checked against live API responses, but I had no browser available in this session, so it has **not been visually inspected**.


## Slack alert (what judges see in the channel)
Six messages per demo run (alert-only): *Needs human review* (ambiguous owner), two *Approval needed* (with the n8n form link), *REFUSED* (protected resource), then *APPROVED* ("the system made NO change") and *REJECTED*. In the optional tag mode a *VERIFIED* message follows too. Alerts that need a person (review, approval, refused, failed, expired) carry the full structure: **Issue / Has to be fixed / Recommendation / Stats (text charts) / Who acts**, plus the fact-checked AI note. Short outcome messages (approved, rejected) stay short. Every message ends with a `ref` id, so a duplicate would be obvious. Every message says it is an alert: nothing is changed or fixed automatically.
