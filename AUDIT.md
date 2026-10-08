# Agentic FinOps Optimizer — Project Audit

**Audited:** 2026-10-08 · **Scope:** whole repository (backend, agents, analytics, cloud integrations, IaC, notifications, React dashboard, tests, Docker/Terraform, docs) · **Size:** ~2,290 lines Python, ~920 lines JSX, ~630 lines tests, ~320 lines Terraform

---

## 1. Executive summary

The project is a well-organised **prototype that presents itself as a working product**. The module layout is clean and the demo-mode UI is polished, but the parts that make it an *agentic FinOps* system — grounded recommendations, human approval, and execution — are either **fabricated, disconnected, or broken**. Several README/SETUP claims are false as written.

| # | Headline | Evidence |
|---|---|---|
| 1 | **The "Act" half does not exist.** `approved_actions` is hard-coded `[]` and nothing ever fills it; `POST /approve` only flips a DB flag yet answers `"queued_for_execution"`. | §3 R-1 |
| 2 | **Recommendations and "savings identified" are LLM inventions.** The recommender sees only `str(anomalies)` and one total-spend line; the tools that find real idle instances / orphaned volumes / rightsizing data are never given to it. | §3 A-1 |
| 3 | **Live mode is broken in several independent ways:** `/forecast` always raises `NameError`; a deployed ECS task silently serves **fake demo data**; the real-AWS setup in SETUP.md cannot work; any undeclared `FINOPS_*` var crashes startup. | §3 R-2, R-3, C-1 |
| 4 | **The default (demo) mode's Approvals page cannot approve anything** — every demo recommendation ID 404s. | §3 R-4 |
| 5 | **No authentication anywhere**, wildcard CORS, deployed on a public IP open to `0.0.0.0/0`, and each call spends money (Cost Explorer + Bedrock). | §3 S-1 |
| 6 | **Dormant but dangerous design:** LLM-written Terraform would be applied with `auto_approve`, and the cleanup templates delete volumes with no snapshot. Safe today only because (1) means it never runs. | §3 S-2, D-1 |
| 7 | **Tests give false confidence:** 2 of 24 fail in a clean environment, and the passing ones mock exactly the paths that are broken (`NameError` is never reached). | §6 |

**Verdict:** not deployable, and not safe to wire up the execution path as designed. The fastest route to something real is **Phase 0 + Phase 1 in §9** (~1–2 weeks of focused work).

**Severity totals:** 5 Critical · 14 High · 20 Medium · 5 Low = 44 findings (full register in §3, one-line index in §10).

---

## 2. Method and what was / wasn't verified

Read every source file in full (Python, JSX, CSS, Terraform, config, docs). Then **executed** the code to confirm suspected defects rather than trusting reading alone.

| Tag | Meaning |
|---|---|
| **[RUN]** | Reproduced by executing code (probe script, test run, `npm audit`/`eslint`/`vite build`). |
| **[READ]** | Established from the source; not executed. |

**Environment used:** throwaway venv outside the repo (Python 3.14, latest unpinned versions: fastapi 0.142, pandas 3.0, langgraph 1.2, pydantic-settings 2.15). Dashboard installed with `npm ci` in a scratch copy. **No files in the repo were modified except this one**; no stray `__pycache__`, DB or `.pytest_cache` was left behind.

**Could not verify** (stated so you can discount accordingly):
- Anything needing **Prophet/NeuralProphet** — not installed (heavy). `tests/test_forecast.py` (3 tests) and `detect_anomalies_prophet` were **not run**; Prophet findings are [READ].
- Anything needing **real AWS / Bedrock / Slack / SMTP** credentials, `terraform`, or Docker. IaC findings are [READ]; `terraform` is not installed here (`which terraform` → none).
- Bedrock model-ID validity (§3 C-2) — reasoned from the ID format, not called.
- Versions differ from what a user's `pip install` would resolve today; the defects below are logic-level and version-independent.

---

## 3. Findings register

Severity: **Critical** = security/safety hole or breaks the primary path · **High** = feature broken or materially misleading · **Medium** = degrades reliability/maintainability · **Low** = polish.

### 3.1 Security

**S-1 · Critical · No authentication/authorization, wildcard CORS, public exposure** [READ]
- `api/main.py:61-67`: `allow_origins=["*"]` with `allow_credentials=True`. No auth dependency on any route.
- `/analyze` and `/ask` trigger Cost Explorer (~$0.01/request) and Bedrock inference; `/approve` mutates approval state (`approved_by` is hard-coded `"api_user"`, `main.py:195`).
- `iac/terraform/deploy_ecs.tf:107` `assign_public_ip = true`, and `:116-128` open ports 8000 **and** 8501 to `0.0.0.0/0`.
- **Impact:** anyone who finds the IP can burn AWS/LLM spend (a cost-amplification attack on a *cost-saving* tool), approve or reject recommendations, and — once execution is wired — trigger infrastructure changes.
- **Fix:** put the API behind an authenticated ALB/API Gateway or add an auth dependency (OIDC/JWT; API key as a stopgap); restrict CORS to the dashboard origin; take approver identity from the token; scope the SG to the ALB; add rate limiting on `/analyze` and `/ask`.

**S-2 · Critical (dormant) · LLM-authored Terraform applied with `auto_approve`** [READ + RUN]
- `agents/recommender.py:110` copies `terraform_plan` straight from LLM JSON; `iac/actions.py:26-51` writes it to `main.tf` and runs `init → plan → apply(auto_approve=True)`. [RUN] confirmed a `null_resource … provisioner "local-exec"` plan passes through `_parse_recommendations` untouched.
- HCL can run arbitrary commands (`local-exec`, `external` data source, `http`) with the process's AWS credentials and filesystem. Prompt content includes AWS-sourced strings (service names, tags in the conversational path), so injection is plausible.
- Dormant today only because of R-1. The moment someone "fixes" R-1 by populating `approved_actions`, this becomes live.
- **Fix:** never execute model-authored code. The LLM should emit a **typed action** (`{type: "stop_instance", instance_id, …}` validated against an allow-list schema); deterministic code (boto3 or *your own* parameterised templates) performs it. See §9 Phase 2.

**S-3 · High · Shell injection in the repo's own destructive templates** [READ]
- `iac/terraform/cleanup_idle.tf:29-30, 37-43, 51-56` interpolate `resource_ids` into `local-exec` shell commands (`aws ec2 terminate-instances --instance-ids ${join(" ", var.resource_ids)}`). `resource_ids` originate from LLM JSON (`recommender.py:109`) and are stored/forwarded without validation. Same for `rightsize_ec2.tf:33-37`.
- **Fix:** validate IDs with `^(i|vol|eipalloc)-[0-9a-f]{8,17}$` before use; prefer native provider resources or boto3 calls over `local-exec`.

**S-4 · High · Secrets can be baked into the Docker image** [READ]
- No `.dockerignore` exists; `Dockerfile:12` is `COPY . .`. A developer's `.env` (and `.git`, `dashboard/node_modules`) ends up in the image that `deploy_ecs.tf` pushes to ECR (`:29 force_delete = true`, `:59 :latest`, mutable tags).
- Image also runs as **root** (no `USER`).
- **Fix:** add `.dockerignore` (`.env`, `.git`, `**/node_modules`, `tests`, `*.db`), add a non-root `USER`, use immutable tags, inject secrets via Secrets Manager (README claims this; the Terraform doesn't do it).

**S-5 · High · Hard-coded credentials and default-credential exposure** [READ]
- `deploy_ecs.tf:65` bakes `postgresql://finops:finops@db:5432/finops` into the task definition (host `db` doesn't exist in ECS; password is the username).
- `docker-compose.yml:31-32, 38-39` publish Postgres (`finops/finops`) and an unauthenticated Redis on `0.0.0.0` of the host.
- `alembic.ini:3` also embeds the same URL (overridden by `env.py`, but invites copy-paste).
- **Fix:** bind to `127.0.0.1:` or drop `ports:`; use `${POSTGRES_PASSWORD}`; use RDS + Secrets Manager in Terraform.

**S-6 · Medium · SMTP STARTTLS without certificate verification; errors swallowed** [READ]
- `notifications/email.py:41` `server.starttls()` with no `ssl.create_default_context()` — `smtplib` falls back to an *unverified* context, so credentials sent at `:43` are MITM-able. `:46-47` `except Exception: return False` hides every failure (no log).
- **Fix:** `server.starttls(context=ssl.create_default_context())`; log the exception.

**S-7 · Medium · HTML injection in alert/report emails** [READ]
- `email.py:63-70, 97-102` interpolate `a['service']`, `r['title']` (LLM/AWS-sourced) into HTML unescaped.
- **Fix:** `html.escape()` every interpolated value.

**S-8 · Medium · IAM policy is both too broad and too narrow** [READ]
- `deploy_ecs.tf:171-200`: `bedrock:InvokeModel` on `Resource = "*"` (scope to the one model/inference-profile ARN). Conversely there are **no write permissions at all**, so the Executor could never succeed as deployed — the infra and the "Act" feature were never designed together.

**S-9 · Low · Unbounded query parameters** [RUN]
- `/forecast?horizon_days=` accepts 0, -5, 100000 (all HTTP 200 in demo mode); in live mode `0` → `ZeroDivisionError` at `analytics/forecast.py:48`. `/history/runs?limit=1000000000` accepted. `AnalysisConfig` in `schemas.py:44` has the right bounds (`ge=7, le=365`) but is **never used**.
- **Fix:** `Query(30, ge=7, le=365)` / `Query(20, ge=1, le=200)`.

---

### 3.2 Reliability & correctness

**R-1 · Critical · Execution path is unreachable; `/approve` is a no-op that lies** [RUN + READ]
- `agents/supervisor.py:95` initialises `approved_actions: []`; `should_execute` (`:29-33`) routes to `execute` only if non-empty. Grep confirms **no code anywhere populates it**.
- `POST /approve` (`main.py:187-203`) sets `approved`, `approved_by`, `approved_at`, commits, and returns `"status": "queued_for_execution"`. Nothing is queued; `executed`, `executed_at`, `execution_status` columns are never written.
- The UI (`ApprovalsPage.jsx:31`) removes the card on success, so users believe the action will happen.
- **Fix (short):** return `"status": "approved"` and stop implying execution until it exists. **Fix (real):** see §9 Phase 2.

**R-2 · High · `GET /forecast` always crashes in live mode** [RUN]
- `main.py:268` calls `forecast_monthly_spend`, which is **never imported** (only `fetch_cost_and_usage`, `pandas`, `date` are, at `:246-248`). Reproduced: `NameError: name 'forecast_monthly_spend' is not defined`.
- The only live-mode test (`test_forecast_insufficient_data`) returns early with an empty dataset, so the line is never reached.
- Related: the endpoint is `async def` but would run a synchronous Prophet fit (P-1); `horizon_days=0` divides by zero (S-9); it re-fetches 90 days of Cost Explorer data **uncached** on every click (P-2).
- **Fix:** `from analytics.forecast import forecast_monthly_spend` (and run it in a threadpool). Add a test with non-empty data.

**R-3 · Critical · "Demo mode" is keyed on a static key that production doesn't have; real credentials are never handed to boto3** [RUN + READ]
- `main.py:42-44`: demo mode ⇔ `not settings.aws_access_key_id`. On ECS (and any role-based setup, which `deploy_ecs.tf` provisions via `ecs_task`), that field is `None` ⇒ **the deployed service serves canned data forever** while `/health` still says `status: "ok"`.
- Conversely every boto3 client (`cost_explorer.py:11`, `cloudwatch.py:9`, `ec2_advisor.py:11`) is built with only `region_name`; `settings.aws_access_key_id/secret` are **never passed** [RUN: grep → False]. So a user who sets `FINOPS_AWS_ACCESS_KEY_ID` (per `.env.example`) flips off demo mode but boto3 has no credentials (pydantic-settings does not export `.env` to `os.environ`) → `NoCredentialsError` on first call.
- **Fix:** make the mode explicit — `FINOPS_DEMO_MODE=true|false` (default false in prod) — and let boto3 use its default credential chain; delete the key fields or pass them through a shared `boto3.Session`.

**R-4 · High · Demo mode's Approvals flow is broken, and demo state is random** [RUN]
- `/recommendations` (demo branch, `main.py:212-217`) calls `generate_demo_analysis("")`, which mints fresh `uuid4`s per call (`demo_data.py:56…`). Approving any listed ID → **HTTP 404** (verified). `pending_only` is ignored (returns 5 regardless); IDs differ on every GET.
- `/history/runs` in demo is `random`-generated each call (verified: two consecutive calls differ), and `POST /analyze` in demo never writes a row (verified: `agent_runs` = 0), so "Run an analysis to populate this view" can never come true.
- `/analyze` demo ignores `request.query`. Demo timestamps are hard-coded to June 2026.
- **Fix:** seed demo rows into the real DB at startup (stable IDs), or make demo data deterministic and make `/approve` demo-aware. Do not mint IDs per request.

**R-5 · High · Pipeline failure modes unhandled; partial state on error** [RUN]
- `/analyze` commits the DB (`main.py:144`) **before** building the response (`:146`). If the model returns `risk_level: "Medium"` or `action_type: "resize"` (not in the `RiskLevel`/`ActionType` enums; `_parse_recommendations` does no normalisation), `RecommendationResponse` raises `ValidationError` → client gets 500, but the rows are already persisted [RUN: row persisted = 1]. Re-running duplicates anomalies (no de-dupe on `AnomalyRecord`).
- No `try/except` around `run_finops_cycle`; `AgentRun.status` values `"running"`/`"failed"` are defined but only `"completed"` is ever written, and scheduled runs write nothing, so History omits both failures and scheduled runs.
- A bad credential/permission (e.g. Cost Explorer not enabled) surfaces as an unformatted 500.
- **Fix:** validate/normalise LLM output into the enums *inside* the recommender; persist `AgentRun(status="running")` first and update in `finally`; one transaction; return a structured 502 on upstream failure.

**R-6 · High · LLM output parsing is brittle and silently lossy** [RUN]
- `recommender.py:97-100` slices from first `[` to last `]`. A response containing prose like `[LOW] risk…` before the JSON → `JSONDecodeError` → `except … pass` → **zero recommendations, no log** [RUN: 0 recs].
- With recent `langchain-aws`, `response.content` can be a **list of content blocks**, not `str` → `AttributeError: 'list' object has no attribute 'find'` [RUN].
- `except (json.JSONDecodeError, ValueError): pass` also swallows a `float()` failure on one bad item and discards the whole batch.
- **Fix:** `llm.with_structured_output(RecommendationList)` (Pydantic) — gives schema validation, retries, and no regex.

**R-7 · High · Idle detection marks instances with *no data* as idle** [RUN]
- `cloud/aws/cloudwatch.py:30-35`: empty `Datapoints` → `avg_cpu = max_cpu = 0.0` → `is_idle: True` **and** `is_oversized: True` [RUN]. A just-launched instance, a metrics gap, or a stopped/replaced instance becomes a "terminate" candidate.
- `ec2_advisor.find_idle_instances(cpu_threshold, days)` / `find_oversized_instances(max_cpu_threshold, …)` accept thresholds that are **ignored** [RUN: False]; the real thresholds (5.0 / 40.0) are hard-coded at `cloudwatch.py:40-41`.
- CPU-only heuristic misclassifies network/memory-bound hosts (NAT, cache, bastion); no check of `DisableApiTermination`, ASG membership, or "keep" tags; instances younger than the lookback window aren't excluded.
- **Fix:** return `None`/`insufficient_data` when `datapoints_count < N`; honour the threshold args; require age ≥ window; add network I/O and tag/ASG guards.

**R-8 · Medium · Cost Explorer & EC2 calls not paginated** [READ]
- `cost_explorer.py:37-42` ignores `NextPageToken`; with 30 days × services × regions grouping, large accounts get silently truncated data, and every downstream statistic is wrong. `ec2_advisor.py:19,57,94` ignore `NextToken` on `describe_*`. Single region only (`settings.aws_region`) — a multi-region fleet is mostly invisible.
- `get_rightsizing_recommendations` (`cost_explorer.py:76-90`) does `["TargetInstances", [{}]][0]`; a present-but-empty list raises `IndexError`.
- **Fix:** use boto3 paginators; loop regions; guard empties.

**R-9 · Medium · Exceptions from the executor are not isolated from approval state** [READ]
- `executor.py:55-61` records failure in-memory only; nothing persists `execution_status`. `iac/actions.py:26` runs in a `TemporaryDirectory`, so **Terraform state is destroyed on exit** (and the returned `working_dir` at `:59` no longer exists). Applied resources can never be planned/destroyed/rolled back by Terraform afterwards.

**R-10 · Medium · `request_approval` is a stub that fails closed by accident** [READ]
- `notifications/slack.py:50` returns `risk_level != "high"`; the executor only calls it for `"high"` (`executor.py:27`), so it *always* returns `False`. Safe by coincidence, but "human approval via Slack" does not exist — there is no callback, no wait, no identity. README's "Slack bot" claim is unsupported. Meanwhile **non-high-risk** actions bypass the gate entirely (`executor.py:27`).
- **Fix:** model approval as a LangGraph `interrupt()` + checkpointer, resumed by the authenticated `/approve` call (see §9).

**R-11 · Medium · DB layer: dual schema management, naive datetimes, dead table** [READ + RUN]
- `init_db()` (`models.py:103-105`, called at startup) runs `create_all` **and** an Alembic migration exists — two sources of truth; Alembic `001` uses `server_default`, models use Python `default`, so schemas diverge subtly (e.g. `resolved` default `"false"` string on SQLite).
- `datetime.utcnow()` is deprecated (warnings in test run) and produces naive timestamps throughout.
- `CostRecord` is **defined and migrated but never read or written** — the "PostgreSQL (cost history)" in README doesn't exist. `Enum as SAEnum` (`:13`) unused.
- `AnomalyRecord.detection_method` and `AnomalyResponse.timestamp` are never persisted; API responses for stored anomalies can't round-trip.
- Default `database_url` is a relative `sqlite:///finops.db` (`settings.py:25`) while `.env.example` says Postgres — behaviour depends on cwd.
- **Fix:** pick Alembic *or* `create_all`; timezone-aware UTC; drop or implement `CostRecord`.

**R-12 · Low · `/health` is not a health check** [READ]
- `agents_ready=True` is hard-coded (`main.py:75`); no DB ping; `cloud_connected` reflects only "is a key string set". Dashboard "Agents Ready" badge (`App.jsx:59-63`) is static and never calls `/health`.

---

### 3.3 Architecture & AI design

**A-1 · Critical · Recommendations are ungrounded; the "savings" metric is fabricated** [READ]
- `recommender.py:55-59` feeds the model `str(anomalies)` plus a single sentence — *"Total spend over period: $X across N days"* (`:88`). It receives **no** resource inventory, no idle-instance list, no rightsizing data, no tags. Yet it must output `resource_ids`, instance types and dollar savings. Those IDs and numbers can only be invented.
- The grounding tools exist (`ec2_advisor.py`, `cost_explorer.get_rightsizing_recommendations`, `find_unattached_elastic_ips`) but are used only by `agents/conversational.py`, which is **dead code** (A-2).
- Because `README` Metrics promise "Savings identified ($)" and "false-positive rate", the headline KPIs would be hallucinated.
- **Fix:** add a deterministic *Inventory* node (idle/oversized/unattached/RI-coverage from real APIs) before `recommend`; let the LLM *rank and explain* real candidates (IDs and $ computed in code), not generate them.

**A-2 · High · The conversational agent is never used; `/ask` just reruns the whole pipeline** [RUN]
- `answer_cost_question` has exactly one hit (its definition). `POST /ask` (`main.py:175`) calls `run_finops_cycle`, returns the templated `report` as the "answer", and hard-codes `sources` and follow-ups (`:178-183`). So "Why did my bill spike?" — the README's headline feature — does not answer the question asked, and each question costs a Cost Explorer pull + Prophet fits + an LLM call.
- **Fix:** route `/ask` to `answer_cost_question` (after fixing P-1) and return the real `sources`.

**A-3 · High · Diagnoser does detection only — no root-cause analysis, and ML defects** [RUN]
README says "Anomaly Detection & **root-cause** agent". `diagnoser.py` only merges detector output. Detector issues:
- **IsolationForest with fixed `contamination=0.05`** (`anomaly.py:14,27`) forces ~5 % of every series to be labelled anomalous regardless of data. [RUN] ±1 % noise → 2 "anomalies" flagged. Combined with `should_recommend` (`supervisor.py:22-26`: any anomaly ⇒ LLM call) **every run calls the LLM**.
- Flags **cost drops** as anomalies (a $100→$10 day is reported with `deviation_pct = -90`, severity "high") [RUN]; the Prophet branch only flags upside (`anomaly.py:76`), so the two detectors disagree on direction.
- **De-dupe key never matches:** IsolationForest emits `'2024-01-21 00:00:00'`, Prophet emits `'2024-01-15'` (`anomaly.py:42` vs `:85`); `diagnoser.py:21-27` keys on `timestamp` ⇒ duplicates for any day both detectors catch [RUN].
- **Division by zero → `inf`:** median 0 (free-tier/credit days) ⇒ `deviation_pct = inf` [RUN], and FastAPI then fails to serialise (`ValueError: Out of range float values are not JSON compliant`) [RUN]. Prophet branch divides by `yhat` (can be ≤0).
- "Expected" = group **median** even for trending series; weekly seasonality ignored by IsolationForest; `np.float64` values leak into dicts.
- No fallback: if Prophet's backend (cmdstan) fails, the entire pipeline raises (`diagnoser.py:17` has no `try`).
- `settings.anomaly_sensitivity` and `AnalysisConfig.anomaly_sensitivity` are both unused.
- **Fix:** replace fixed-contamination IF with a robust z-score/MAD or Prophet-only upside rule; floor the denominator; normalise timestamps with `str(ts.date())`; catch detector failures; add a materiality threshold (absolute $ as well as %).

**A-4 · Medium · Not a real agent loop; no checkpointing / human-in-the-loop primitive** [READ]
- `supervisor.py` is a straight pipeline (`observe → diagnose → recommend → [execute] → report`), no tool-calling, no loop, no persistence. LangGraph's `interrupt`/checkpointer — the intended mechanism for approval gates — is not used. Node functions mutate `state["messages"]` in place (`diagnoser.py:33-37`, `executor.py:69`, `recommender.py:63`) instead of returning deltas.
- `BackgroundTasks` imported, never used; long cycles run inside the HTTP request (frontend timeout 120 s, `client.js:7`).

**A-5 · Medium · LLM provider abstraction is advertised but absent** [RUN]
- `llm_provider` ("bedrock | openai | azure"), `openai_api_key`, and the Azure/OpenAI README row exist only in config; agents hard-code `ChatBedrock` (`recommender.py:35`, `conversational.py:85`). No `temperature`, `max_tokens`, timeout, or retry configured; `tenacity` is installed but unused.

**A-6 · Medium · Feature inventory vs reality** — see §7 table (GCP, Redis, S3, SQS/EventBridge, LangSmith, Prometheus, Pulumi, NeuralProphet, Streamlit are all absent or unwired).

---

### 3.4 Configuration & onboarding

**C-1 · High · `.env` handling: documented setup can't work, and valid-looking settings crash the app** [RUN]
- `config/settings.py:42` uses `env_prefix="FINOPS_"` and pydantic-settings' default `extra="forbid"`:
  - **SETUP.md §4 tells users to put `AWS_ACCESS_KEY_ID=…` / `OPENAI_API_KEY=…` in `.env`.** Those are *ignored* [RUN: `aws_access_key_id = None | openai_api_key = None`] → the app stays in demo mode while the guide claims it connected to AWS.
  - Setting any `FINOPS_*` variable that `Settings` doesn't declare **crashes at import**: `FINOPS_SMTP_HOST` and `FINOPS_ALERT_EMAIL_RECIPIENTS` both raise `ValidationError: Extra inputs are not permitted` [RUN].
- Consequence: **email/alert settings are impossible to configure.** `email.py:21-25` and `scheduler.py:38,60` read `smtp_*`, `email_from`, `alert_email_recipients`, `report_email_recipients` via `getattr(settings, …, default)` — none are declared, so they are always the default (`[]`/`None`) and **no email is ever sent**; trying to enable them crashes startup. Weekly report (`scheduler.py:54`) is also never scheduled (only `scheduled_analysis` runs under `__main__`).
- **Fix:** declare the fields in `Settings`; correct SETUP.md to the `FINOPS_` names (or `AliasChoices`); add `extra="ignore"`.

**C-2 · Medium · Bedrock model ID is likely invalid** [READ — verify]
- `.env.example:12` and `settings.py:21`: `anthropic.claude-sonnet-4-20250514`. Bedrock IDs carry a version suffix (`…-v1:0`), and newer Claude models are typically invoked through a regional **inference profile** ID (`us.anthropic…`). As written, the first LLM call is likely a `ValidationException`. Confirm in the Bedrock console and prefer reading the ID from config only (no code default).

**C-3 · Medium · Dead/unused settings** [RUN]
`max_auto_savings_threshold`, `anomaly_sensitivity`, `redis_url`, `llm_provider`, `openai_api_key`, `langsmith_*`, `gcp_*` (outside the unwired GCP module) have no consumers. Delete or implement; unused knobs imply behaviour (e.g. auto-approve under $100) that does not exist.

---

### 3.5 Performance & scalability

**P-1 · High · Blocking work inside `async def`** [READ]
- All boto3 calls are synchronous (`cost_explorer.py`, `cloudwatch.py`, `ec2_advisor.py`) inside `async def`; Prophet fits are CPU-bound (`diagnoser.py:17`, one per `(service, region)` group — dozens of multi-second fits sequentially, `anomaly.py:58-70`); SQLAlchemy sessions are sync; `smtplib` is sync. One `/analyze` call freezes the event loop for every other request (including `/health`).
- `cloudwatch.py:19-35` issues one `GetMetricStatistics` per instance, serially (N+1). 500 instances ≈ 500 round-trips.
- **Fix:** `asyncio.to_thread` / `run_in_threadpool` for boto3 + Prophet, or a worker (Celery/RQ/ECS task); batch CloudWatch via `GetMetricData`; cap Prophet to top-N cost groups.

**P-2 · Medium · No caching; every UI action repeats paid API calls** [READ]
`/forecast` re-fetches 90 days; `/analyze` and `/ask` re-fetch 30 days. `CostRecord` + a daily ingest would make all of this a DB read. Ironic for a cost tool: Cost Explorer is $0.01/request.

---

### 3.6 Infrastructure & DevOps

**I-1 · High · Deployment artefacts don't match the app** [READ]
- Dashboard is now **React/Vite** but `docker-compose.yml:23` and `deploy_ecs.tf:80` still run `streamlit run dashboard/app.py` — **that file does not exist**. The compose `dashboard` service and the ECS `dashboard` container would crash on start. The React app has no Dockerfile/hosting at all.
- `Dockerfile` installs no `terraform` and no AWS CLI, yet `iac/actions.py` shells out to `terraform` and the templates call `aws ec2 …` (`which terraform` → none even locally).
- `Dockerfile` installs `curl` but defines no `HEALTHCHECK`; ECS service has no load balancer, health check, or TLS; `desired_count=1`.
- Compose `depends_on` doesn't wait for Postgres readiness, `init_db()` runs at startup, no `restart:` policy ⇒ race on `docker-compose up`.

**I-2 · Medium · Terraform quality** [READ]
No `required_providers`/version pins, no remote backend (state is local), `ecs_task` role lacks any write perms (S-8), `ecr force_delete = true`, mutable `:latest`, containers have no `secrets`. `rightsize_ec2.tf` / `cleanup_idle.tf` use `null_resource + local-exec` — imperative scripts that bypass Terraform's state model; re-apply is a no-op and there is no rollback. `iac/actions.py:63-82 generate_rightsizing_tf` (dead code) would declare a *new* `aws_instance` (no AMI, ignores `instance_id`) rather than modify the existing one.

**I-3 · Medium · No CI, no lint/type config for Python, no pre-commit** [READ]
No `.github/`, no ruff/black/mypy config, no Makefile. Frontend lint currently **fails** (F-3).

**I-4 · Low · Repo hygiene** [READ + RUN]
- Directory has **no `.git` of its own** (it's a zip download); `git rev-parse --show-toplevel` resolves to the user's **home directory** repo, so any git command here silently operates on that repo. Initialise a dedicated repo.
- 3 × `.DS_Store` present; `.gitignore` doesn't list it. No `LICENSE`. `dashboard/README.md` is Vite boilerplate.

---

### 3.7 Frontend

**F-1 · High · Forecast chart is fabricated client-side** [RUN: eslint `react-hooks/purity`]
- `ForecastPage.jsx:26-36` builds the plotted series with `Math.random()`:
  `projected = daily_average ± 15 % random`, `upper/lower = CI/horizon ± 10 random`. The API returns **only totals**, so the chart is noise around the average, regenerated on every render (it visibly re-jitters when the slider moves). The page header claims "Prophet time-series models". The comment even says "Generate mock chart data".
- **Fix:** have `/forecast` return the per-day `forecast_df` (already computed in `forecast.py:45`) and plot that; remove `Math.random()`.

**F-2 · Medium · Dependency vulnerabilities** [RUN: `npm audit`]
9 advisories (**8 high**, 1 moderate); `npm audit fix` is available for all. Direct: **axios** (prototype-pollution gadgets in fetch adapter/default instance) and **react-router-dom** (via react-router RSC CSRF bypass — RSC mode is not used here, so low practical exposure). Transitive: postcss, browserslist, nanoid, brace-expansion, source-map-js, baseline-browser-mapping (mostly build-time). Practical risk is low-to-moderate for an internal tool; fix anyway.

**F-3 · Medium · Lint fails (11 errors) [RUN]**
Unused imports (`useState`, `LayoutDashboard` in `App.jsx`; `Clock` in `ApprovalsPage.jsx`; `Scatter`, `ScatterChart`, `ZAxis` in `HistoryPage.jsx`), 3 × impure `Math.random()` in render (F-1), 2 × `setState` in effect (`ApprovalsPage.jsx:25`, `HistoryPage.jsx:28`). `npm run build` **succeeds** (712 modules, 842 kB JS / 251 kB gzip — single chunk, no code-splitting).

**F-4 · Medium · "Full Report" renders raw Markdown** [READ]
`AnalysisPage.jsx:188` prints `result.report` in a plain `<div>`; `.report-content` has no `white-space: pre-wrap` and no Markdown renderer (AskPage uses `ReactMarkdown`, Analysis doesn't). Headings/bullets collapse into one paragraph.

**F-5 · Low · Misc**
- Typo'd export `analyzeCoasts` (`client.js:11`, `AnalysisPage.jsx:7`).
- `vite.config.js:8-14` configures an `/api` proxy that nothing uses — `client.js:3` calls `http://localhost:8000` directly (so CORS is load-bearing, S-1).
- Approve button on destructive types (`terminate`) has no confirmation or detail view (resource IDs, plan) — only a count (`ApprovalsPage.jsx:84`).
- No error boundary, no auth UI, no tests; `toFixed()` on possibly-null API numbers.

---

### 3.8 Dependencies & packaging

**X-1 · Medium · `requirements.txt` is unpinned, duplicated, and bloated** [RUN]
- All `>=`, no lockfile ⇒ non-reproducible builds.
- Duplicates: `boto3` (lines 9, 22), `alembic` (39, 60), `structlog` (51, 62 — conflicting minimums), `pytest`/`pytest-asyncio`/`pytest-cov` (55-57, 65-67).
- **17 declared packages are never imported:** `streamlit, plotly, neuralprophet, statsmodels, scipy, slack-sdk, redis, prometheus-client, pyyaml, openai, langchain-community, langsmith, google-cloud-billing, tenacity, moto, python-dotenv, langchain`. `neuralprophet` pulls in **PyTorch** (very large image). Test-only packages (`pytest*`, `moto`) ship in the runtime image.
- **Missing:** `pydantic-settings` is imported at `config/settings.py:3` but not listed — it only arrives transitively (probably via `langchain-community`); removing that "unused" package would break startup.
- SETUP.md says Python 3.9+, but current `langgraph` needs ≥3.10; Dockerfile uses 3.12. SETUP's tree lists `ml/` and `db/`, which don't exist (real: `analytics/`, `api/models.py`).

---

### 3.9 Maintainability

**M-1 · Medium · Roughly 15–20 % of the Python (~400 of ~2,290 lines, estimated) is dead or test-only** [RUN: grep]
Defined, never called from application code: `answer_cost_question` (whole `conversational.py`, 165 lines), `fetch_gcp_billing` (GCP module), `cluster_workloads`, `forecast_by_service`, `generate_rightsizing_tf`, `find_unattached_elastic_ips`, `find_oversized_instances` (only imported by dead code), `weekly_report` + `send_savings_report`, `CostRecord`, `AnalysisConfig`. The Observer fetches AWS only, so GCP and clustering contribute nothing.

**M-2 · Low · Docs drift** — see §7. `SETUP.md` says "All 24 tests should pass" (true only with an AWS key set — §6).

---

## 4. What is good

- Clear separation: `agents/` · `analytics/` · `cloud/` · `api/` · `notifications/` · `iac/`; `TypedDict` state; Pydantic request validation on `/analyze`/`/ask` (`min_length=1, max_length=2000`).
- Terraform wrapper runs `plan` before `apply` and in a temp dir; a high-risk human gate was *intended* (and fails closed, if by accident).
- Demo mode is a good idea for onboarding (the implementation is what needs work, R-3/R-4).
- Cost-aware details: EBS price table, EIP cost note, rightsizing via Cost Explorer's own recommendations.
- Dashboard builds cleanly; consistent visual language; sensible chart choices; loading/error/empty states on most pages.
- Analytics tests exist and cover the severity classifier, stable-data no-anomaly, clustering, forecasting.

---

## 5. Data-flow trace (what actually happens)

```
UI "Run Analysis" → POST /analyze
  └─ demo?  (no FINOPS_AWS_ACCESS_KEY_ID)  → canned JSON, nothing persisted        ← default
  └─ live:  observe  (CE get_cost_and_usage, 30d, unpaginated)
            diagnose (IsolationForest ∥ Prophet; merge-dedupe broken)               ← typically ≥1 anomaly
            recommend (ChatBedrock; prompt = str(anomalies) + 1 line)               ← hallucinated IDs/$
            should_execute → approved_actions == [] → skip                           ← execution unreachable
            report (f-string template, no LLM narrative)
          → INSERT agent_run/anomalies/recommendations; COMMIT; build response (may 500)
UI "Approve" → POST /approve → set flag → {"status":"queued_for_execution"}           ← nothing queued
```

---

## 6. Test suite assessment

**Result [RUN]:** `pytest` (excluding Prophet-dependent `test_forecast.py`, not installable here): **19 passed, 2 failed** in a clean environment.

| Failing test | Why |
|---|---|
| `test_api.py::TestAnalyzeEndpoint::test_analyze_returns_report` | Test doesn't set the AWS key ⇒ app is in demo mode ⇒ returns 4 canned anomalies instead of the mocked 2. |
| `test_api.py::TestForecastEndpoint::test_forecast_insufficient_data` | Same: demo mode returns 200 instead of 400. |

With `FINOPS_AWS_ACCESS_KEY_ID=x` set, **all 6 API tests pass** [RUN] — i.e. the suite passes or fails depending on the developer's shell, and CI would fail.

**Why the green tests are misleading**
- The one live forecast test uses empty data and exits before the `NameError` (R-2).
- `/analyze` test feeds a hand-made, perfectly valid LLM result — the enum/validation failure (R-5) and parsing failures (R-6) are never exercised.
- Nothing tests: `executor.py`, `recommender._parse_recommendations`, `iac/actions.py`, `notifications/*`, `scheduler.py`, demo mode, DB models/migrations, or any frontend code. `test_agents.py` checks routing functions only, not nodes.
- `moto` is installed but unused; AWS tests mock `boto3` wholesale.

**Quality issues:** module-level `app.dependency_overrides[...]` (`test_api.py:21`) leaks global state; unseeded `np.random` in `test_anomaly.py`, `test_clustering.py`, `test_forecast.py` (flaky potential); `test_forecast_with_trend` computes `historical_last_30_avg` and never uses it, asserting only `> 0`; `test_anomaly.py` redefines the `sample_cost_data` fixture, shadowing `conftest.py`; `pytest.ini` marker block is redundant with `asyncio_mode=auto`. The 21-test run took ~71 s wall-clock.

**Add first (highest value/effort):** (1) live `/forecast` with data; (2) `/analyze` with out-of-enum LLM output; (3) parse-failure cases; (4) no-datapoint EC2 case; (5) demo approve round-trip; (6) force demo mode explicitly in a fixture so tests don't depend on env.

---

## 7. README / SETUP claims vs reality

| Claim | Reality |
|---|---|
| Executor applies approved changes via Terraform/CloudFormation | Unreachable (R-1); no CloudFormation anywhere |
| Slack/email **approval gates** | Slack stub, always `False` (R-10); email can't be configured (C-1) |
| Root-cause diagnosis | Detection only (A-3) |
| LLM-grounded reports with $ savings | Ungrounded (A-1) |
| "Why did my bill spike?" Q&A grounded in Cost Explorer | Conversational agent unused (A-2) |
| GCP BigQuery billing | Module exists, never called |
| Prophet / **NeuralProphet** | NeuralProphet never imported |
| Azure OpenAI / OpenAI | Config only; Bedrock hard-coded (A-5) |
| PostgreSQL cost history | `CostRecord` table never used (R-11) |
| **Redis** agent state, **S3** reports, **EventBridge→SQS** streaming | Not implemented |
| LangSmith traces, Prometheus + Grafana | Not implemented (packages installed, unused) |
| IaC: python-terraform **and Pulumi** | Pulumi absent |
| **Streamlit** dashboard (`dashboard/app.py`) | Replaced by React; `app.py` doesn't exist; compose/ECS still reference it (I-1) |
| README metrics: precision/recall, FP rate, agent latency | No instrumentation |
| SETUP: "set `AWS_ACCESS_KEY_ID` in `.env` to connect AWS" | Ignored by settings (C-1) |
| SETUP: "All 24 tests should pass" | 2 fail without an AWS key (§6) |
| SETUP project tree (`ml/`, `db/`) | Directories don't exist |
| README "Secrets Manager", "EventBridge rules", "S3" in deployment | Not in the Terraform |

---

## 8. Risk matrix

| Area | Today | If someone "just wires up" execution |
|---|---|---|
| Security | Public, unauthenticated, spend-burning API | **Remote infra changes by anyone; LLM-authored code execution** |
| Data integrity | Wrong/fabricated savings; partial writes on error | Wrong resources terminated (no-data ⇒ "idle", no snapshot) |
| Availability | Event loop blocked by Prophet/boto3 | Same, plus half-applied state with no Terraform state |
| Trust | Demo data looks real; fake forecast chart; false "queued" status | Fake approval UX leads to unsafe reliance |

---

## 9. Remediation roadmap

**Phase 0 — stop the bleeding (≈1–2 days).** Small, safe, high value.
1. Import `forecast_monthly_spend`; bound `horizon_days`/`limit` (R-2, S-9).
2. Replace `_is_demo_mode` with an explicit `FINOPS_DEMO_MODE`; use boto3's default credential chain (R-3).
3. `/approve` returns `"approved"`/`"rejected"`, not `"queued_for_execution"` (R-1).
4. Fix `Settings`: declare email fields, `extra="ignore"`, correct SETUP.md and `.env.example` (C-1); add `pydantic-settings` to requirements.
5. Add `.dockerignore`, non-root `USER`; remove/fix the `streamlit` service and container (S-4, I-1); bind compose ports to localhost.
6. Add API auth + restrict CORS; restrict the security group (S-1).
7. Pin and de-duplicate `requirements.txt`; drop the 17 unused packages; split `requirements-dev.txt` (X-1).
8. Make tests independent of env (force demo mode in fixtures); add the 6 tests in §6; add CI running `pytest`, `ruff`, `npm run lint && npm run build`.

**Phase 1 — make "Observe → Diagnose → Recommend" true (≈1 week).**
1. Add an *Inventory* node using the existing advisor tools; ground the recommender with real candidates; compute `$` and IDs in code (A-1).
2. `with_structured_output` + enum-validated models (R-5, R-6); fix model ID (C-2); set `temperature`, timeouts, retries.
3. Rework anomaly detection: drop fixed-contamination IF, upside-only with materiality floor, safe denominators, normalised timestamps, try/except per detector (A-3).
4. Fix idle detection to require sufficient data (R-7); paginate CE/EC2; loop regions (R-8).
5. Wire `/ask` to the conversational agent (A-2).
6. Move boto3/Prophet off the event loop; batch CloudWatch with `GetMetricData`; add a daily ingest into `CostRecord` and read from DB (P-1, P-2).
7. Persist `AgentRun` first/finally; record scheduled runs; single transaction; de-dupe anomalies (R-5, R-11).
8. Frontend: plot real per-day forecast, render report Markdown, call `/health`, `npm audit fix`, clean lint (F-1…F-5).

**Phase 2 — a safe "Act" (≈2+ weeks; do not start before Phase 0-1 + auth).**
- Model each action as a **typed, allow-listed schema**; executed by deterministic code (boto3 or *your own* parameterised IaC), never model-authored HCL (S-2, S-3).
- Mandatory **preflight** (re-check idle/unattached at execution time, tags like `keep`, ASG/`DisableApiTermination`), **snapshot before delete**, dry-run output shown to the approver, **audit log** (who/when/what/result), idempotency keys, and a rollback story (R-9).
- Approval as a LangGraph `interrupt()` + persistent checkpointer, resumed by the authenticated `/approve` (R-10); Slack buttons as an additional approver channel with signed callbacks.
- Least-privilege, per-action IAM role assumed only at execution time; separate from the read-only analysis role (S-8).

**Phase 3 — product hardening.** Observability (structured logs with request IDs, metrics for the README KPIs), HTTPS/ALB, RDS + Secrets Manager, Alembic-only schema, load tests on multi-hundred-instance accounts.

---

## 10. Finding index

| ID | Sev | One-line |
|---|---|---|
| S-1 | Critical | No auth, wildcard CORS, public SG, paid endpoints |
| S-2 | Critical* | LLM-authored Terraform applied with auto-approve (*dormant) |
| R-1 | Critical | Execution unreachable; `/approve` returns false "queued_for_execution" |
| R-3 | Critical | Demo-mode flag wrong for ECS; real creds never passed to boto3 |
| A-1 | Critical | Recommendations/savings hallucinated; no grounding |
| S-3 | High | Shell injection in cleanup/rightsize templates |
| S-4 | High | No `.dockerignore`; `.env` can ship in image; root user |
| S-5 | High | Hard-coded `finops:finops` creds; DB/Redis exposed in compose |
| R-2 | High | Live `/forecast` → `NameError` |
| R-4 | High | Demo Approvals 404; random demo state |
| R-5 | High | Commit-before-validate; 500 with persisted rows; no failure records |
| R-6 | High | Brittle LLM JSON parsing; list-content crash |
| R-7 | High | No CloudWatch data ⇒ "idle"; threshold args ignored |
| A-2 | High | Conversational agent dead; `/ask` reruns pipeline |
| A-3 | High | Detector flaws: forced 5 % anomalies, drops flagged, dedupe broken, `inf` |
| C-1 | High | SETUP's `.env` ignored; undeclared `FINOPS_*` crashes; email unconfigurable |
| P-1 | High | Blocking boto3/Prophet inside async handlers; N+1 CloudWatch |
| I-1 | High | Compose/ECS reference non-existent Streamlit app; no terraform/aws CLI in image |
| F-1 | High | Forecast chart is `Math.random()` |
| S-6, S-7, S-8, R-8, R-9, R-10, R-11, A-4, A-5, A-6, C-2, C-3, P-2, I-2, I-3, F-2, F-3, F-4, X-1, M-1 | Medium | see sections |
| S-9, R-12, I-4, F-5, M-2 | Low | see sections |

*Count:* Critical 5 · High 14 · Medium 20 (S-6, S-7, S-8, R-8, R-9, R-10, R-11, A-4, A-5, A-6, C-2, C-3, P-2, I-2, I-3, F-2, F-3, F-4, X-1, M-1) · Low 5 (S-9, R-12, I-4, F-5, M-2) = 44. Test-suite issues in §6 are tracked separately and not counted.

---

## 11. Reproduction notes

Probe script and scratch venv lived in the session scratchpad (outside the repo). To re-verify the key claims quickly:

```bash
# R-2: live forecast NameError
FINOPS_AWS_ACCESS_KEY_ID=x python -c "
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from api.main import app
row=lambda d:{'time_period':{'start':d,'end':d},'groups':[{'keys':['EC2','us-east-1'],'metrics':{'UnblendedCost':{'Amount':'10'}}}]}
with patch('cloud.aws.cost_explorer.fetch_cost_and_usage',new_callable=AsyncMock) as m:
    m.return_value={'results_by_time':[row('2024-01-01'),row('2024-01-02')]}
    TestClient(app).get('/forecast')"          # NameError

# R-4: demo approvals 404
python -c "from fastapi.testclient import TestClient as T; from api.main import app; c=T(app); i=c.get('/recommendations').json()[0]['id']; print(c.post('/approve',json={'recommendation_id':i,'approved':True}).status_code)"   # 404

# C-1: undeclared FINOPS_ var crashes
echo 'FINOPS_SMTP_HOST=x' > .env && python -c "from config import settings"   # ValidationError: extra_forbidden

# F-2/F-3: frontend
cd dashboard && npm ci && npm audit && npx eslint . && npm run build
```
