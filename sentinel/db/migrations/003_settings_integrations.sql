-- Per-tenant, human-editable settings and integration requests. Both tables are tenant-scoped by row-level security.
-- Nothing here can widen what the system may change: ACTION_MODE and every safety limit stay in the server environment.
-- The app role gets no DELETE (same invariant as the rest of the schema): "off" is a flag, not a missing row.
create table tenant_settings (
  tenant_id  text primary key references tenants,
  settings   jsonb not null default '{}',
  updated_by text not null,
  updated_at timestamptz not null default now()
);

create table integration_interest (
  tenant_id    text not null references tenants,
  provider     text not null check (provider ~ '^[a-z][a-z0-9_]{1,30}$'),
  active       boolean not null default true,
  requested_by text not null,
  updated_at   timestamptz not null default now(),
  primary key (tenant_id, provider)
);

alter table tenant_settings      enable row level security;
alter table integration_interest enable row level security;
create policy tenant_scope on tenant_settings      using (app_tenant_ok(tenant_id)) with check (app_tenant_ok(tenant_id));
create policy tenant_scope on integration_interest using (app_tenant_ok(tenant_id)) with check (app_tenant_ok(tenant_id));
grant select, insert, update on tenant_settings, integration_interest to sentinel_app;
