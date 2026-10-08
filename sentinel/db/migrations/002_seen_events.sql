-- Delivery bookkeeping for n8n workflows: which outbox events a workflow has fully handled.
-- Lives in the API database (not n8n static data, which this n8n version does not persist from Code nodes and which
-- loses updates under concurrent executions). No business data: only opaque event ids.
create table n8n_seen_events (
  event_id text primary key check (event_id ~ '^evt_[A-Za-z0-9_]{1,64}$'),
  workflow text not null,
  seen_at  timestamptz not null default now()
);
grant select, insert on n8n_seen_events to sentinel_app;
