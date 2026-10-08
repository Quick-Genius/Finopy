# Architecture and contracts

```
 LocalStack ──CloudQuery (aws v22.19.2 → pg dest, source-built)──▶ PostgreSQL inventory_raw
     ▲                                                                    │ read-only
     │ live re-read + action                                              ▼
 Custodian runner ◀──signed grant── API (FastAPI + LangGraph) ──outbox──▶ n8n ──▶ approver (local form)
   (only holder of                  tenant-scoped, RLS                     │
    action creds)                   state machine, audit                   └─signed callback─▶ API /approve
```

| Container | Job | Holds |
|---|---|---|
| `api` | cases, evidence, policy compiler, approval gate, audit, dashboard API | RLS-bound DB role only; **no AWS variables, no DB owner URL** |
| `runner` | Custodian validate / dry-run / execute / read-only inspect | the only (dummy) LocalStack credentials used for actions |
| `cq-runner` | triggered CloudQuery sync (HTTP wrapper) | read credentials, writes `inventory_raw` only |
| `n8n` | schedule, intake routing, human wait, timeout, callback | HMAC secret + automation token via env, no cloud access |
| `postgres` | system of record (+ n8n DB) | named volume |
| `localstack` | the only "cloud" | ephemeral |
| `dashboard` | React/Vite UI behind nginx | nothing |

Redis is **not used**: the per-tenant/window lock is `pg_try_advisory_xact_lock` plus `UNIQUE(tenant, window, snapshot)` and `UNIQUE(tenant, idempotency_key)`.

## Case graph (LangGraph, `app/graph.py`)
`ingest → baseline → detect → attribute → assess → recommend`, all deterministic. There is no LLM node: money is `Decimal`, scores are computed from a weekday-seasonal median/MAD baseline, owners come only from reference-data rows. `ActionIntent` (`app/assess.py`) is the only structure a model could ever fill in; it forbids extra fields, restricts template/action pairs, and validates IDs with `^(i|vol)-[0-9a-f]{8,17}$`. Unit tests show YAML, shell and destructive-action payloads are rejected.

## State machine (`app/states.py`, compare-and-set + audit event per transition)
```
NEW → INVESTIGATED → DRY_RUN → PENDING → APPROVED            (default, alert-only: APPROVED is the end; a human fixes it)
                                      └→ EXECUTED → VERIFIED   (only with ACTION_MODE=tag_after_approval)
        │ ├─ DISMISSED (legitimate)       │ ├─ REJECTED   └─ EXECUTION_FAILED   └─ VERIFICATION_FAILED
        │ ├─ REFUSED (guards)             │ ├─ EXPIRED
        │ └─ POLICY_INVALID               │ └─ INVESTIGATED (approval invalidated by a new case version)
```
`needs_review` is an *assessment*, not a state: such cases stay `INVESTIGATED` and `dry-run` refuses them.

## Tenancy
- Principal = bearer token → `principals` row → `(tenant_id, role)`. A tenant in a body/query is never read (extra fields are 422).
- Humans: every query runs in a transaction with `app.tenant_id` set; RLS (`app_tenant_ok`) hides all other tenants; unset = zero rows. A foreign `case_id` is a 404 (no existence oracle).
- Automation (n8n): `principals.scope` lists tenants; the tenant of an operation is the one stored **on the case/approval row**. Callback actors must belong to that tenant (checked server-side) and be an approver/reviewer with the correct PIN.
- Composite FKs `(case_id, tenant_id)` make cross-tenant references impossible; the app role has **no UPDATE/DELETE** on append-only tables (`case_versions, evidence, policies, audit_events, idempotency_keys`), plus triggers that stop even the owner.

## Webhooks and endpoints n8n is connected to
| Direction | Endpoint | Auth | Purpose |
|---|---|---|---|
| → n8n | `POST /webhook/sentinel-case` | HMAC over raw body + 300 s window + `event_id` dedupe | `case.created` / `case.updated` intake |
| → n8n | `POST /webhook/sentinel-approval` | same | `approval.requested` |
| → n8n | `POST /webhook/sentinel-notify` | same | `case.state_changed` outcome notifications (Slack) |
| → n8n | `POST /webhook/sentinel-run-now` | `x-run-now-secret` header | manual analysis (the hourly Schedule Trigger is the other entry) |
| n8n → | `POST cq-runner:8080/sync` | bearer | CloudQuery sync, returns the completion record |
| n8n → API | `POST /internal/analyze` | automation token | ingest snapshot + analyse tenants in scope |
| n8n → API | `GET /cases/{id}`, `GET /approvals/{id}` | automation token | fetch evidence / approval packet |
| n8n → API | `POST /cases/{id}/dry-run` | automation token | compile, validate, dry-run, freeze, request approval |
| n8n → API | `POST /approvals/{id}/form-url` | automation token | publish the local form link for the dashboard |
| n8n → API | `POST /approve` | automation token **and** HMAC | decision callback (`approve`/`reject`/`timeout`) |
| n8n → API | `GET/PUT /internal/events/{event_id}` | automation token | delivery dedupe: check before, mark **only after** success |

## Dashboard endpoints (human principals; tenant always from the token)
| Endpoint | Roles | Purpose |
|---|---|---|
| `GET /overview` | analyst, approver, reviewer, finance | one read model for the dashboard: totals, savings by workload/owner, 45-day spend, pending approvals, recent audit events (`overview.py`; money as 2-decimal strings) |
| `GET /integrations` | same | catalog with live status: AWS inventory freshness, n8n outbox health, runner, DB role (RLS), AI summaries; Slack is reported as managed in n8n because the API cannot observe it |
| `PUT /integrations/{provider}/interest` | same | register or withdraw interest in a **planned** connector (per tenant, audited). Live connectors answer 409, unknown ones 404 |
| `GET /settings` | same | effective workspace settings, defaults, options, `can_edit`, and the read-only server guardrails |
| `PUT /settings` | reviewer | change which outcomes notify (`REFUSED/APPROVED/REJECTED/EXPIRED/VERIFIED`) and the minimum projected saving. Strict schema (`extra=forbid`), audited with before/after |

Settings only tune *who is told*. `POLICY_INVALID`, `EXECUTION_FAILED` and `VERIFICATION_FAILED` can never be muted, and `ACTION_MODE` and every safety limit stay in the server environment, so no setting can widen what the system may change. The rule is applied where the outbox event is written (`states.transition`), so a muted outcome is never queued; the audit event is written regardless. Tables `tenant_settings` and `integration_interest` (migration 003) are under row-level security with no DELETE grant.

Every response carries `x-request-id` (a valid inbound id is echoed), `cache-control: no-store`, `x-content-type-options: nosniff` and `referrer-policy: no-referrer`; non-health requests are access-logged with that id.

## Event contracts (schema_version `1.0`)
`case.created` / `case.updated` (blueprint example, extended): `event_id` (`evt_` + sha256(case|version|type)[:20], stable), `case_id`, `case_version`, `tenant_id`, `observed_at` (UTC), `signal{service, actual, expected, currency, anomaly_score}`, `attribution{owner, confidence, evidence[]}`, `assessment`, `recommendation{route, action, template, monthly_saving, currency, risk}`, `callback.case_url`.
`case.state_changed` (REFUSED, POLICY_INVALID, APPROVED, REJECTED, EXPIRED, VERIFIED, VERIFICATION_FAILED, EXECUTION_FAILED): `event_id`, `occurred_at`, `tenant_id`, `case_id`, `case_version`, `from_state`, `to_state`, `actor`, `summary{workload, kind, owner, risk, classification, projected_monthly}`, `detail{reasons[], note, matched_ids[], verification_ok}`. Whitelisted and truncated in `app/states.py::notify_payload`; no policy text, PINs or tokens. Written to the outbox in the same transaction as the transition.
`approval.requested`: `event_id`, `tenant_id`, `case_id`, `case_version`, `approval_id`, `policy_sha256`, `target_set_sha256`, `expires_at`, `required_role`.
Delivery: row committed in the **same transaction** as the case; dispatcher POSTs canonical JSON with `x-sentinel-timestamp/-signature/-event-id`; retries with exponential back-off (≤120 s); at-least-once.

**n8n failure semantics (learned the hard way on n8n 1.123.84):** if a node fails after the webhook trigger, n8n answers the webhook **200 with an empty body**, which the outbox would take as "delivered" and the event would be lost. So every call that must succeed has an *error output* wired to a `Respond 500` node; the outbox then retries and the event is **not** marked seen. Dedupe lives in the API (`n8n_seen_events`, `GET/PUT /internal/events/{id}`), not in n8n workflow static data, which this version does not persist from Code nodes and which would lose updates under concurrent executions. Duplicates are therefore skipped by n8n, and the real no-double-action guarantees remain in the API (state compare-and-set, `UNIQUE(approval_id)` on executions, idempotency keys).

**Approval callback** (`POST /approve`): `approval_id, case_id, decision, actor_id, actor_proof, case_version, policy_sha256, target_set_sha256, expires_at, idempotency_key`. Checks, in order: HMAC + replay window → idempotency (same canonical request ⇒ stored result with `replayed:true`; same key, different request ⇒ 409) → approval exists → actor in the case's tenant with approver/reviewer role and PIN → approval `requested` and case `PENDING` → case version → both digests equal the stored ones **and** re-hash from the stored policy text and target list → echoed expiry → not expired (late ⇒ `EXPIRED`) → transition + durable `executions` row (`UNIQUE(approval_id)`). Targets, policy and tenant are never read from the payload. Refusals are audited and do not consume the idempotency key.

**Action mode.** `ACTION_MODE=alert_only` (default) is enforced three times: the approval handler creates no execution (it audits `approval.recorded_no_action`), the executor refuses, and the Custodian runner answers HTTP 403 to `/execute` regardless of what it is sent. The paragraph below applies only to `ACTION_MODE=tag_after_approval`.

**Execution** (worker, `app/execution.py` + `runner/server.py`): preflight in the API (approval/state/version/digests/expiry/target cap) → signed grant → the runner independently re-checks the allow-list, both digests, expiry, its own hard cap (5), re-reads live resources (tenant tag, no `Protected`, not `production`, eligible state), re-runs a live Custodian dry-run that must equal the approved targets **exactly**, writes an at-most-once marker, then runs the policy. Afterwards the API performs a fresh read-only `inspect`; `VERIFIED` only if the expected tag is observed and was absent before. Runner timeout ⇒ `inconclusive`, never retried.

## Audit export
`GET /audit/export` returns the caller's tenant as NDJSON; `GET /cases/{id}` carries the same timeline. Not hash-chained (see gaps).
