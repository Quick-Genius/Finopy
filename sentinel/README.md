# FinOps Sentinel

A local, governed **Observe → Reason → Prove → Approve → Act → Verify** loop for cloud-cost anomalies, built from the corrected PRD (v2) and the blueprint. Two isolated demo tenants (`payments`, `analytics`); everything runs against **LocalStack** with dummy credentials. No paid or real cloud is reachable.

```
CloudQuery inventory → deterministic anomaly detection + owner attribution → allow-listed intent
→ Cloud Custodian validate + dry-run (exact targets, SHA-256 of policy and target set)
→ human approval through n8n (signed, idempotent callback), recorded for audit
→ a person fixes it by hand. DEFAULT (`ACTION_MODE=alert_only`): the system changes NOTHING.
   Optional (`ACTION_MODE=tag_after_approval`): one reversible tag by the isolated runner, then a live re-check → VERIFIED.
```

## Run it
Requires Docker (Compose v2) and ~8 GB for the engine. Tested on Apple silicon (Colima).
```bash
cd sentinel
./scripts/up.sh        # creates .env with random secrets, builds, starts, waits for health
./scripts/replay.sh    # reset to the seeded scenario, run the whole loop via n8n, print PASS/FAIL proof
./scripts/reset.sh     # reset only (wipes DB, n8n state, LocalStack; keeps the CloudQuery plugin cache)
```
First build is slow (the PostgreSQL destination plugin is compiled with Go; n8n image is large). Then: dashboard http://localhost:3000 · n8n http://localhost:5678 (`demo@sentinel.local` / `Sentinel-Demo-1`) · API http://localhost:8000/docs.
Trigger a run: `curl -X POST localhost:5678/webhook/sentinel-run-now -H "x-run-now-secret: $(grep RUN_NOW_SECRET .env | cut -d= -f2)"` (an hourly schedule exists too).

## What is seeded (deterministic, relative to the seed date)
| Tenant | Scenario | Outcome |
|---|---|---|
| payments | stable RDS spend with weekday seasonality | no case |
| payments | checkout Lambda spike inside a known launch event | `DISMISSED` — legitimate |
| payments | two forgotten idle dev instances (spend jump + idle CPU) | alert → `PENDING` → human approves → `APPROVED` (decision recorded, nothing changed). With `tag_after_approval`: → `VERIFIED` |
| payments | idle protected production DR instance | `REFUSED` before approval |
| payments | idle instance whose Owner tag conflicts with its deployment record | `needs_review`, no owner invented |
| analytics | unattached EBS volume | `PENDING` (rejected in the proof; a second template, tag-only) |

## Tests and proofs (all were run; results are in `proof/`)
```bash
python3 scripts/e2e.py            # ~58 live checks (fewer when Slack is real or the optional tag mode is off): scenarios, cross-tenant denial, n8n approval, one action, replay, audit
python3 scripts/e2e.py --outbox   # n8n down during analysis: nothing lost, no duplicates
APPROVAL_TTL_SECONDS=25 ./scripts/up.sh && python3 scripts/e2e.py --expiry   # silence never approves
docker compose exec -T -e STACK_TESTS=1 -e N8N_API_TOKEN=$(grep N8N_API_TOKEN .env|cut -d= -f2) api python -m pytest tests -q
```
`tests/test_logic.py` (27, pure logic) and `tests/test_stack.py` (live DB/API: RLS, append-only, callback refusals) → 38 passed, 1 skipped.

## Slack notifications (optional)
n8n can post to a Slack channel; with no URL set it stays silent and nothing else changes.
1. Rotate the Slack app client secret and revoke the exposed old bot/user tokens and app-configuration tokens before reinstalling. The setup screenshots exposed credentials; do not reuse those values.
2. The helper requests only Slack's `incoming-webhook` scope. Install it into the intended workspace and select `#aws-sentinel`. No `chat:write` scope is needed for these one-way messages.
3. Run `python3 scripts/slack_connect.py` in `sentinel/`. It prompts for the client ID and rotated secret (the secret input is hidden). Open its temporary authorization link in a browser signed into the workspace and allow the webhook installation. The helper stores the webhook in `sentinel/.env` and restricts that file to the owner.
4. Run `docker compose up -d n8n`, then `python3 scripts/e2e.py --slack-ping` to post a clearly-labelled synthetic test message. Never paste the webhook URL into chat or commit it.

Messages: **Approval needed** (exact targets, projected saving, rollback, expiry, link to the n8n form), **Needs human review**, **REFUSED / POLICY_INVALID**, **APPROVED** ("the system made NO change"), **REJECTED**, **EXPIRED**, and, only in the optional tag mode, **VERIFIED** ("change observed in live state"), **EXECUTION_FAILED / VERIFICATION_FAILED**. No PINs, secrets or policy text are ever included. The form link is `localhost:5678`, so it opens only on the machine running the stack. These are channel alerts; interactive Slack buttons are not implemented. Slack trouble never blocks an approval; outcome notifications retry until Slack answers (a permanent 4xx from Slack is recorded, not retried forever).
Test without Slack: `./scripts/slack_mock.sh start`, then `SLACK_WEBHOOK_URL=http://slack-mock:9000/ docker compose up -d n8n` and run `python3 scripts/e2e.py` (adds 9 Slack checks).

## Docs
[Phase 0 feasibility + licence](docs/PHASE0.md) · [AUDIT.md claims vs source](docs/AUDIT-VERIFICATION.md) · [Architecture, webhooks, event contracts](docs/ARCHITECTURE.md) · [3-minute demo script + production gaps](docs/DEMO.md)

## Provenance and licence
The reference repository (`AdiiiC/Agentic-FinOps-Optimizer`) has **no licence**, so none of its code was copied or modified; this tree is an independent re-implementation (see PHASE0 §4 for the honest "not a strict clean room" caveat). Third-party components keep their own licences (CloudQuery MPL-2.0, Custodian Apache-2.0, n8n Sustainable Use License). This project has no licence file yet — add one before distributing.


## What the system is for (and what a Slack alert contains)
FinOps Sentinel **investigates and alerts**. It does not fix cloud waste by itself: people decide and fix. By default it takes **no action at all**: an approval is only recorded for the audit trail. (Optional mode `ACTION_MODE=tag_after_approval` lets the isolated runner add ONE reversible tag after a named human approves; even then it never stops, resizes or deletes anything.)

Every alert is written for a human, in this order:
- **Issue**: what happened, with numbers (spend vs expected with % change, idle CPU).
- **Has to be fixed**: what is wrong and what it costs per month (projected, never claimed as realized).
- **Recommendation**: advice for people (confirm the owner, approve to record the decision, or, for protected or ownerless resources, what humans should do instead).
- **Stats**: text charts that work in plain Slack (14-day cost sparkline with the analysis window marked, expected-vs-actual bars, per-instance CPU on a fixed 0-10% scale). Picture charts would need a Slack bot token with file-upload rights; the dashboard has the real chart.
- **Who acts**: the human step (approve/reject in the form, resolve ownership, or review only).
- **Investigation summary**: an AI-written note (Alibaba Qwen) built only from verified evidence; every number, resource id and owner in it is fact-checked, and a plain template is used if the check fails or the AI is down.
The issue, fix, recommendation and stats are computed by code (`api/app/briefing.py`); the AI never produces them.
