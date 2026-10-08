# FinOps Sentinel

A local, governed **Observe → Reason → Prove → Approve → Act → Verify** loop for cloud-cost anomalies, built from the corrected PRD (v2) and the blueprint. Two isolated demo tenants (`payments`, `analytics`); everything runs against **LocalStack** with dummy credentials. No paid or real cloud is reachable.

```
CloudQuery inventory → deterministic anomaly detection + owner attribution → allow-listed intent
→ Cloud Custodian validate + dry-run (exact targets, SHA-256 of policy and target set)
→ human approval through n8n (signed, idempotent callback) → ONE non-destructive Custodian action
→ live re-query of the resource → VERIFIED (or an honest failure)
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
| payments | two forgotten idle dev instances (spend jump + idle CPU) | full loop → `VERIFIED` |
| payments | idle protected production DR instance | `REFUSED` before approval |
| payments | idle instance whose Owner tag conflicts with its deployment record | `needs_review`, no owner invented |
| analytics | unattached EBS volume | `PENDING` (rejected in the proof; a second template, tag-only) |

## Tests and proofs (all were run; results are in `proof/`)
```bash
python3 scripts/e2e.py            # 33 live checks: scenarios, cross-tenant denial, n8n approval, one action, replay, audit
python3 scripts/e2e.py --outbox   # n8n down during analysis: nothing lost, no duplicates
APPROVAL_TTL_SECONDS=25 ./scripts/up.sh && python3 scripts/e2e.py --expiry   # silence never approves
docker compose exec -T -e STACK_TESTS=1 -e N8N_API_TOKEN=$(grep N8N_API_TOKEN .env|cut -d= -f2) api python -m pytest tests -q
```
`tests/test_logic.py` (27, pure logic) and `tests/test_stack.py` (live DB/API: RLS, append-only, callback refusals) → 38 passed, 1 skipped.

## Docs
[Phase 0 feasibility + licence](docs/PHASE0.md) · [AUDIT.md claims vs source](docs/AUDIT-VERIFICATION.md) · [Architecture, webhooks, event contracts](docs/ARCHITECTURE.md) · [3-minute demo script + production gaps](docs/DEMO.md)

## Provenance and licence
The reference repository (`AdiiiC/Agentic-FinOps-Optimizer`) has **no licence**, so none of its code was copied or modified; this tree is an independent re-implementation (see PHASE0 §4 for the honest "not a strict clean room" caveat). Third-party components keep their own licences (CloudQuery MPL-2.0, Custodian Apache-2.0, n8n Sustainable Use License). This project has no licence file yet — add one before distributing.
