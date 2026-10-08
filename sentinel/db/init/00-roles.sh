#!/bin/sh
# Runs once on first Postgres start (empty data dir). POSTGRES_USER=sentinel is the migration/seed owner.
# sentinel_app is what the API uses: not a superuser, no BYPASSRLS, so row-level security applies to it.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
create role sentinel_app login password '${APP_DB_PASSWORD}' nosuperuser nobypassrls;
create role cloudquery   login password '${CQ_DB_PASSWORD}'  nosuperuser nobypassrls;
create role n8n          login password '${N8N_DB_PASSWORD}' nosuperuser nobypassrls;
create database n8n owner n8n;
create schema inventory_raw authorization cloudquery;
grant usage on schema inventory_raw to sentinel_app;
alter default privileges for role cloudquery in schema inventory_raw grant select on tables to sentinel_app;
SQL
