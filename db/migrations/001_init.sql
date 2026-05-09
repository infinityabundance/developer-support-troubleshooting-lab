-- Baseline schema. Loaded automatically by the postgres image's
-- /docker-entrypoint-initdb.d hook on first container start (see the
-- volume mount in docker-compose.yml).
--
-- Anything in this file runs exactly once, against a fresh database,
-- before the api container is even started. To re-run it after the
-- first boot you have to `docker compose down -v` (the -v drops the
-- db volume).

-- schema_migrations is the registry that case 02's escalation argues
-- should exist; the migration runner (api/main.py::admin_migrate)
-- inserts a row on every successful migration, and the api reads
-- MAX(version) when /healthz?check=schema is hit. With this table in
-- place, "did this migration land here?" is one query instead of a
-- multi-environment diff.
--
-- The lab seeds version 1 here at boot because 001 IS the bootstrap;
-- a real schema-migration tool would insert this row from outside,
-- but for the lab the simplest place is at the end of the bootstrap
-- SQL itself. The broken state for case 02 is "version 2 has not
-- been inserted because the migration runner skipped this node."
CREATE TABLE schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO schema_migrations (version) VALUES (1);

-- Customer / order schema for cases 02 (audit_log lives in 002), 05
-- (N+1 join over orders×customers), and any future case that needs
-- realistic-shaped data.
CREATE TABLE customers (
    id   SERIAL PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE orders (
    id           SERIAL PRIMARY KEY,
    customer_id  INTEGER NOT NULL REFERENCES customers(id),
    amount_cents INTEGER NOT NULL
);

-- Ten customer fixture rows (deliberately fictional / parody names so
-- nobody mistakes them for real customer data). Case 05 uses these
-- as the join target for the N+1 demonstration.
INSERT INTO customers (name) VALUES
    ('Acme Corp'), ('Globex'), ('Initech'), ('Umbrella'), ('Soylent'),
    ('Stark Industries'), ('Wayne Enterprises'), ('Wonka'), ('Tyrell'), ('Cyberdyne');

-- 200 random orders distributed across the 10 customers. random() in
-- Postgres is per-row in a generate_series, so each row gets a
-- different customer_id and a different amount. 200 rows is the
-- magic number that makes case 05's N+1 visible — at limit=200 the
-- broken /orders fires 201 queries (1 + 200), which is enough delta
-- from the fixed /orders/v2's 2 queries to make the bug obvious.
INSERT INTO orders (customer_id, amount_cents)
SELECT (random() * 9 + 1)::int, ((random() * 49000) + 1000)::int
FROM generate_series(1, 200);

-- 002_partial.sql is intentionally NOT applied at boot; case 02
-- reproduces the failure mode where 002 was forgotten on a replica.
-- The compose volume mount only exposes 001 to /docker-entrypoint-
-- initdb.d/; 002 is mounted at /migrations/ in the api container and
-- applied via /admin/migrate/2 when explicitly requested.
