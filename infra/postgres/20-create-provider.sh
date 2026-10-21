#!/bin/sh
# Adds the isolated demo-provider database only when explicitly configured.
# Safe to rerun on existing volumes: does not reset passwords or application data.
set -eu
if [ -z "${DEMO_PROVIDER_DB_PASSWORD:-}" ]; then
  echo 'DemoProvider database not configured; skipping demo-only initialization.'
  exit 0
fi
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v provider_password="$DEMO_PROVIDER_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE demo_provider LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE', :'provider_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'demo_provider') \gexec
SELECT 'CREATE DATABASE demo_provider OWNER demo_provider'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'demo_provider') \gexec
REVOKE ALL ON DATABASE demo_provider FROM PUBLIC;
SQL
