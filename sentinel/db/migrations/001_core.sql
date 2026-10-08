-- FinOps Sentinel core schema. Every tenant-owned table carries tenant_id, has row-level security,
-- and child tables reference their parent through (id, tenant_id) so a row can never point across tenants.

create table tenants (
  tenant_id     text primary key check (tenant_id ~ '^[a-z][a-z0-9_-]{1,30}$'),
  name          text not null,
  owner_group   text not null,
  policy_profile jsonb not null default '{}',
  created_at    timestamptz not null default now()
);

-- Authentication lookup happens before the tenant is known, so principals is not under RLS.
create table principals (
  principal_id  text primary key,
  tenant_id     text references tenants,
  role          text not null check (role in ('analyst','approver','reviewer','finance','automation')),
  display_name  text not null,
  token_sha256  text not null unique,
  pin_hash      text,
  scope         text[] not null default '{}',
  check ((role = 'automation') = (tenant_id is null))
);

-- tenant visibility: either the transaction is bound to one tenant, or it is an explicit system transaction.
create function app_tenant_ok(t text) returns boolean language sql stable as $$
  select current_setting('app.system', true) = 'on' or t = current_setting('app.tenant_id', true)
$$;

create function forbid_mutation() returns trigger language plpgsql as $$
begin raise exception '% is append-only (%)', tg_table_name, tg_op; end $$;

-- ---------------------------------------------------------------- reference data
create table owners (
  tenant_id text not null references tenants, owner_id text not null, display_name text not null,
  primary key (tenant_id, owner_id)
);
create table account_map (
  account_id text primary key, tenant_id text not null, owner_id text not null,
  foreign key (tenant_id, owner_id) references owners
);
create table deployments (
  tenant_id text not null references tenants, resource_ref text not null, owner_id text not null,
  source text not null, deployed_at timestamptz not null,
  primary key (tenant_id, resource_ref), foreign key (tenant_id, owner_id) references owners
);
create table naming_rules (
  tenant_id text not null references tenants, kind text not null check (kind in ('name','vpc')),
  pattern text not null, owner_id text not null,
  primary key (tenant_id, kind, pattern), foreign key (tenant_id, owner_id) references owners
);
create table known_events (
  event_id text primary key, tenant_id text not null references tenants, workload text not null,
  start_date date not null, end_date date not null, kind text not null, description text not null,
  expected_uplift_pct numeric(6,2) not null
);

-- ---------------------------------------------------------------- facts
create table cost_facts (
  fact_id     bigserial primary key,
  tenant_id   text not null references tenants,
  usage_date  date not null,
  service     text not null,
  workload    text not null,
  resource_id text not null default '',
  owner_tag   text,
  amount      numeric(14,4) not null check (amount >= 0),
  currency    char(3) not null check (currency ~ '^[A-Z]{3}$'),
  unique (tenant_id, usage_date, service, workload, resource_id)
);
create index on cost_facts (tenant_id, workload, usage_date);

create table utilization_facts (
  tenant_id text not null references tenants, resource_id text not null, usage_date date not null,
  cpu_avg numeric(6,2) not null, primary key (tenant_id, resource_id, usage_date)
);

create table inventory_snapshots (
  snapshot_id text primary key,
  tenant_id   text not null references tenants,
  sync_id     text not null,
  source      text not null,
  status      text not null check (status in ('complete','failed')),
  plugin_versions jsonb not null default '{}',
  sync_started_at timestamptz, sync_finished_at timestamptz,
  resource_count int not null, unscoped_count int not null default 0,
  created_at  timestamptz not null default now(),
  unique (snapshot_id, tenant_id)
);
create table inventory_resources (
  snapshot_id text not null, tenant_id text not null, resource_id text not null,
  resource_type text not null check (resource_type in ('ec2_instance','ebs_volume')),
  region text not null, account_id text not null, state text not null,
  tags jsonb not null default '{}', launch_time timestamptz, attached boolean,
  name text, vpc_id text,
  primary key (snapshot_id, resource_id),
  foreign key (snapshot_id, tenant_id) references inventory_snapshots (snapshot_id, tenant_id)
);

create table analysis_runs (
  run_id text primary key, tenant_id text not null references tenants,
  idempotency_key text not null, window_start date not null, window_end date not null,
  snapshot_id text not null, status text not null check (status in ('running','completed','failed')),
  started_at timestamptz not null default now(), finished_at timestamptz, summary jsonb,
  unique (tenant_id, idempotency_key),
  unique (tenant_id, window_start, window_end, snapshot_id),
  foreign key (snapshot_id, tenant_id) references inventory_snapshots (snapshot_id, tenant_id)
);

-- ---------------------------------------------------------------- cases
create table cases (
  case_id text primary key,
  tenant_id text not null references tenants,
  dedupe_key text not null,
  state text not null check (state in ('NEW','INVESTIGATED','DRY_RUN','PENDING','APPROVED','EXECUTED','VERIFIED',
        'REJECTED','EXPIRED','DISMISSED','REFUSED','POLICY_INVALID','EXECUTION_FAILED','VERIFICATION_FAILED')),
  current_version int not null,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  unique (case_id, tenant_id), unique (tenant_id, dedupe_key)
);
create table case_versions (
  case_id text not null, tenant_id text not null, version int not null,
  snapshot_id text not null, content jsonb not null, content_sha256 text not null,
  created_at timestamptz not null default now(),
  primary key (case_id, version), unique (case_id, version, tenant_id),
  foreign key (case_id, tenant_id) references cases (case_id, tenant_id) deferrable initially deferred
);
create trigger case_versions_immutable before update or delete on case_versions
  for each row execute function forbid_mutation();

create table evidence (
  evidence_id text primary key, case_id text not null, case_version int not null, tenant_id text not null,
  type text not null, source_ref text not null, payload jsonb not null, payload_sha256 text not null,
  created_at timestamptz not null default now(),
  foreign key (case_id, case_version, tenant_id) references case_versions (case_id, version, tenant_id)
);
create trigger evidence_immutable before update or delete on evidence
  for each row execute function forbid_mutation();

create table policies (
  policy_id text primary key, case_id text not null, case_version int not null, tenant_id text not null,
  template text not null, template_version int not null,
  status text not null check (status in ('valid','refused','invalid')),
  policy_yaml text not null, policy_sha256 text not null,
  candidate_ids jsonb not null, target_ids jsonb not null, target_set_sha256 text not null,
  excluded jsonb not null default '[]', refusal_reasons jsonb not null default '[]',
  guards jsonb not null default '{}',
  validate_log text, dryrun_log text, dryrun_exit_code int, runner_versions jsonb,
  dryrun_at timestamptz not null, created_at timestamptz not null default now(),
  foreign key (case_id, case_version, tenant_id) references case_versions (case_id, version, tenant_id)
);
create trigger policies_immutable before update or delete on policies
  for each row execute function forbid_mutation();

create table approvals (
  approval_id text primary key, case_id text not null, case_version int not null, tenant_id text not null,
  policy_id text not null references policies,
  status text not null check (status in ('requested','approved','rejected','expired','invalidated')),
  required_role text not null, policy_sha256 text not null, target_set_sha256 text not null,
  requested_at timestamptz not null default now(), expires_at timestamptz not null,
  decided_at timestamptz, actor_id text, actor_role text, note text, form_url text,
  foreign key (case_id, tenant_id) references cases (case_id, tenant_id)
);
-- at most one live approval per case
create unique index approvals_one_live on approvals (case_id) where status in ('requested','approved');

create table idempotency_keys (
  key text primary key, tenant_id text not null references tenants, request_sha256 text not null,
  status_code int not null, response jsonb not null, created_at timestamptz not null default now()
);

create table executions (
  execution_id text primary key, case_id text not null, tenant_id text not null,
  approval_id text not null unique references approvals,           -- one execution per approval, ever
  attempt int not null default 1,
  status text not null check (status in ('queued','running','succeeded','failed','refused','inconclusive')),
  policy_id text not null references policies, policy_sha256 text not null, target_set_sha256 text not null,
  created_at timestamptz not null default now(), started_at timestamptz, ended_at timestamptz,
  exit_code int, matched_ids jsonb, result jsonb, logs text, verification jsonb, verified_at timestamptz,
  foreign key (case_id, tenant_id) references cases (case_id, tenant_id)
);

create table audit_events (
  audit_id bigserial primary key, tenant_id text not null references tenants, case_id text,
  event_type text not null, actor text not null, from_state text, to_state text, case_version int,
  correlation_id text, detail jsonb not null default '{}', created_at timestamptz not null default clock_timestamp()
);
create index on audit_events (tenant_id, case_id, audit_id);
create trigger audit_immutable before update or delete on audit_events
  for each row execute function forbid_mutation();

create table outbox (
  event_id text primary key, tenant_id text not null references tenants, case_id text,
  event_type text not null, schema_version text not null, payload jsonb not null,
  created_at timestamptz not null default now(), attempts int not null default 0,
  next_attempt_at timestamptz not null default now(), delivered_at timestamptz, last_error text
);
create index on outbox (next_attempt_at) where delivered_at is null;

-- ---------------------------------------------------------------- row-level security + grants
do $$
declare t text;
begin
  foreach t in array array['owners','deployments','naming_rules','known_events','cost_facts','utilization_facts',
      'inventory_snapshots','inventory_resources','analysis_runs','cases','case_versions','evidence','policies',
      'approvals','idempotency_keys','executions','audit_events','outbox']
  loop
    execute format('alter table %I enable row level security', t);
    execute format('create policy tenant_scope on %I using (app_tenant_ok(tenant_id)) with check (app_tenant_ok(tenant_id))', t);
  end loop;
end $$;
-- account_map is global by design (an account belongs to exactly one tenant) and read-only for the app.

grant usage on schema public to sentinel_app;
grant usage on all sequences in schema public to sentinel_app;
grant select on tenants, principals, account_map to sentinel_app;
grant select, insert on owners, deployments, naming_rules, known_events, cost_facts, utilization_facts,
      inventory_snapshots, inventory_resources, analysis_runs, case_versions, evidence, policies, audit_events,
      idempotency_keys to sentinel_app;
grant select, insert, update on cases, approvals, executions, outbox to sentinel_app;
-- the app role is never granted DELETE or TRUNCATE on anything.
