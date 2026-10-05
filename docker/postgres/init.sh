#!/usr/bin/env bash
set -Eeuo pipefail

psql --no-psqlrc --set ON_ERROR_STOP=1 --set VERBOSITY=terse \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv dev_password DEV_DB_PASSWORD
\getenv test_password TEST_DB_PASSWORD
CREATE ROLE kazan_dev LOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION NOBYPASSRLS PASSWORD :'dev_password';
CREATE ROLE kazan_test LOGIN NOSUPERUSER NOCREATEROLE CREATEDB NOREPLICATION NOBYPASSRLS PASSWORD :'test_password';
CREATE DATABASE kazan_courts_dev OWNER kazan_dev;
REVOKE ALL ON DATABASE kazan_courts_dev FROM PUBLIC;
REVOKE ALL ON DATABASE kazan_courts_dev FROM kazan_test;
SQL
