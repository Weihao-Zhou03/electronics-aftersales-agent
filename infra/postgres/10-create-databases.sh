#!/bin/sh
# Only runs for a new volume; never resets an existing database.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v aftersales_password="$AFTERSALES_DB_PASSWORD" -v chatwoot_password="$CHATWOOT_DB_PASSWORD" <<'SQL'
CREATE ROLE aftersales LOGIN PASSWORD :'aftersales_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE chatwoot LOGIN PASSWORD :'chatwoot_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE DATABASE aftersales OWNER aftersales;
CREATE DATABASE chatwoot OWNER chatwoot;
REVOKE ALL ON DATABASE aftersales FROM PUBLIC;
REVOKE ALL ON DATABASE chatwoot FROM PUBLIC;
SQL
# Chatwoot's schema requires these extensions; runtime role stays non-superuser.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname chatwoot <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname aftersales <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
SQL
