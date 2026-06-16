#!/usr/bin/env bash
#
# Postgres full-stack smoke for the adapter-agnostic DB-integration features
# (S15-3, #406 — closes the ADR-0022 portability work).
#
# Proves that `valdo reconcile`, `valdo extract`, and `valdo db-compare` all run
# against a live PostgreSQL backend (DB_ADAPTER=postgresql), via the Sprint-11
# docker-compose full-stack (db = postgres:16, migrate, valdo).
#
# The `valdo` service already carries DB_ADAPTER=postgresql + DB_* pointing at
# the `db` service, so we seed a small table in `db` and run the three commands
# inside the `valdo` container with `docker compose exec`.
#
# Usage (from repo root, Docker running):
#     bash demo/pg_fullstack_smoke.sh
#
# Tear down afterwards with:  docker compose down -v
#
# No secrets, no published DB port, no tracked runtime DB (the compose pg volume
# is gitignored / removed by `down -v`).
set -euo pipefail

MAPPING_HOST="demo/pg_smoke_customers_mapping.json"
MAPPING_CTR="/tmp/pg_smoke_customers_mapping.json"

echo "==> Bring up the full-stack (db + migrate + valdo) ..."
# Either entry point works; the setup script wraps the same compose bring-up:
#   bash scripts/valdo-setup.sh --env full-stack
docker compose up -d --build

echo "==> Wait for the valdo service to report healthy ..."
for _ in $(seq 1 30); do
  status=$(docker compose ps --format '{{.Service}} {{.Health}}' | awk '/^valdo /{print $2}')
  [ "${status:-}" = "healthy" ] && break
  sleep 3
done
docker compose ps

echo "==> Seed a small CUSTOMERS table in Postgres (mixed types incl. a native boolean) ..."
docker compose exec -T db psql -U valdo -d valdo -v ON_ERROR_STOP=1 <<'SQL'
DROP TABLE IF EXISTS customers;
CREATE TABLE customers (
  customer_id   INTEGER      NOT NULL,
  customer_name VARCHAR(50)  NOT NULL,
  balance       NUMERIC(12,2),
  active_flag   BOOLEAN
);
INSERT INTO customers (customer_id, customer_name, balance, active_flag) VALUES
  (1, 'ALICE', 100.50, true),
  (2, 'BOB',   250.00, false),
  (3, 'CAROL',   0.00, true);
SQL

echo "==> Copy the smoke mapping into the valdo container ..."
docker compose cp "$MAPPING_HOST" "valdo:$MAPPING_CTR"

echo
echo "=========== 1) RECONCILE (DB_ADAPTER=postgresql) ==========="
# Expect: "Mapping is valid", 4 mapped / 4 DB columns. The `boolean` mapping
# field reconciles clean against the PG native boolean (CanonicalType, ADR 0022).
docker compose exec -T valdo valdo reconcile -m "$MAPPING_CTR"

echo
echo "=========== 2) EXTRACT (DB_ADAPTER=postgresql) ==========="
# Expect: "Extracted 3 rows", pipe-delimited file (header + 3 rows).
docker compose exec -T valdo valdo extract -t customers -o /tmp/customers_extract.txt
docker compose exec -T valdo sh -c 'echo "--- extract output ---"; cat /tmp/customers_extract.txt'

echo
echo "=========== 3) DB-COMPARE (DB_ADAPTER=postgresql) ==========="
# Round-trip: extract the table to a file, then compare the same table against
# it -> expect PASS (3 matching rows, 0 diffs). balance is cast to text so the
# NUMERIC scale renders deterministically (pandas would otherwise drop trailing
# zeros: 100.50 -> 100.5 — the value-formatting class tracked as #426, not a
# portability failure: the row sets match 1:1 either way).
docker compose exec -T valdo valdo extract \
  -q "SELECT customer_id, customer_name, to_char(balance,'FM999999990.00') AS balance, active_flag FROM customers ORDER BY customer_id" \
  -o /tmp/customers_cmp.txt
docker compose exec -T valdo valdo db-compare \
  -q "SELECT customer_id, customer_name, to_char(balance,'FM999999990.00') AS balance, active_flag FROM customers ORDER BY customer_id" \
  -m "$MAPPING_CTR" \
  -f /tmp/customers_cmp.txt \
  -k customer_id

echo
echo "==> Smoke complete. Tear down with:  docker compose down -v"
