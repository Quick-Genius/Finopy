# AUDIT.md claims checked against the upstream source

Method: I **read** the cited upstream files and grepped for call sites. I did **not run** the upstream application, so runtime reproductions in AUDIT.md (the `NameError`, the 404s, the persisted-row-on-500) are *consistent with the code but not re-executed by me*. Paths are relative to the upstream folder `Agentic-FinOps-Optimizer-main/`.

| Priority item | Verdict | Evidence in source |
|---|---|---|
| No tenant / auth boundary (S-1) | **Confirmed** | `api/main.py:61-67` `allow_origins=["*"]` with `allow_credentials=True`; no auth dependency on any route in `main.py`; approver hard-coded `"api_user"` at `main.py:195`; no `tenant` concept in `api/models.py` (all tables unscoped); `iac/terraform/deploy_ecs.tf:107` public IP, `:120,127,134` open to `0.0.0.0/0`. |
| `/approve` only flips a flag (R-1) | **Confirmed** | `main.py:194-197` sets `approved/approved_by/approved_at`; returns `"queued_for_execution"` at `main.py:202`. Nothing is queued; `executed*`/`execution_status` columns (`models.py:74-76`) are never written. |
| Execution path unreachable (R-1) | **Confirmed** | `agents/supervisor.py:95` initialises `"approved_actions": []`; `should_execute` (`:29-33`) only routes to `execute` if non-empty. `grep approved_actions` finds only `supervisor.py:31,95`, `executor.py:19`, `state.py:40`: nothing ever populates it. |
| LLM-authored Terraform (S-2) | **Confirmed (dormant)** | `agents/recommender.py:110` copies `terraform_plan` straight from model JSON; `iac/actions.py:26-30` writes it to `main.tf`; `:47-51` `tf.apply(..., auto_approve=True)`. `iac/terraform/cleanup_idle.tf:28-29` / `:37-40` shell out `aws ec2 terminate-instances` / `delete-volume` with interpolated IDs (S-3). |
| Unstable IDs (R-4) | **Confirmed** | `recommender.py:103` `uuid4()` per parsed recommendation; `api/demo_data.py:56,66,76,86,96` mint fresh `uuid4()` on every call of the demo generator, and `/recommendations` in demo mode calls it per request (`main.py:212-217`), so an ID returned to the UI cannot be found by `/approve` (`main.py:190`). |
| Fake dashboard data (F-1) | **Confirmed** | `dashboard/src/pages/ForecastPage.jsx:32-34` build the plotted series with `Math.random()`; `api/demo_data.py:203-206` `random.randint/uniform/choice` for history. |
| README claims Streamlit but UI is React/Vite | **Confirmed** | `dashboard/package.json` (react, vite, recharts); `dashboard/src/*.jsx`. `docker-compose.yml:23` and `deploy_ecs.tf:80` still run `streamlit run dashboard/app.py`; no `dashboard/app.py` exists. |
| Demo-mode flag wrong for role-based creds (R-3) | **Confirmed by reading** | `main.py:42-44` demo mode = `not settings.aws_access_key_id`; `main.py:76` reports `cloud_connected` from the same key. |
| `/forecast` NameError (R-2) | **Confirmed by reading** | `main.py:268` calls `forecast_monthly_spend`; the function-local imports at `:246-248` don't include it. |
| Recommendations ungrounded (A-1) | **Confirmed** | `recommender.py:55-59` passes `str(anomalies)` + a one-line total (`:88`); no inventory/idle data reaches the prompt. |
| AUDIT I-4 "no `.git` in this folder" | **Stale** | A `.git` directory now exists in the supplied folder (two commits). I ran no git command, per instruction. |

Where the new build addresses each item: tenant/auth → `api/app/auth.py`, `db.py`, `db/migrations/001_core.sql` (RLS); approval gate → `app/approval.py`; reachable, guarded action → `app/execution.py` + `runner/server.py`; no model-authored code → `app/assess.py::ActionIntent`, `app/policy.py::render`; stable IDs → slug-derived `case_id`, `sha256`-derived `event_id`; real data → `GET /series`, `GET /summary`, no `random` anywhere (`grep -R "random\|Math.random" api dashboard/src` is empty).
