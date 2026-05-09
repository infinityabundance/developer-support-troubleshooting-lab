-- Migration 002. Applied only when case 02 is *fixed* (i.e. when the
-- operator runs `curl -X POST .../admin/migrate/2` explicitly). The
-- bug case 02 reproduces is that this migration was never run on the
-- affected node — the audit_log table the api expects to read from
-- doesn't exist.
--
-- Two unrelated objects ship in the same migration because they
-- happened to land in the same release: a real-world artefact of
-- shipping changes in batches. Future cases that need either object
-- can rely on it being present after migrate/2 has run.
--
-- Both DDL statements use IF NOT EXISTS so the migration is
-- idempotent: re-applying it via /admin/migrate/2 (which reset.sh
-- + reproduce-all routinely do) doesn't error. Idempotence at the
-- DDL level matters because the migration runner's INSERT INTO
-- schema_migrations uses ON CONFLICT DO NOTHING — the SQL must also
-- tolerate re-execution or we'd get inconsistent state.

-- Case 02's missing table. /audit reads from this; if the table is
-- absent, psycopg raises UndefinedTable → api returns 500.
CREATE TABLE IF NOT EXISTS audit_log (
    id         SERIAL PRIMARY KEY,
    action     TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Four fixture rows so /audit returns something interesting after the
-- migration is applied (rather than an empty array, which is correct
-- behavior but anticlimactic for the post-fix demo).
INSERT INTO audit_log (action) VALUES
    ('user.created'), ('user.login'), ('order.placed'), ('order.refunded');

-- Case 05's optional index: not required for /orders/v2's fix to
-- work (the WHERE id = ANY(...) hits the customers PK index, not
-- orders.customer_id), but useful for any other query that joins
-- orders to customers via customer_id. Ships in 002 because that's
-- where it landed historically; could equally live in its own
-- migration.
CREATE INDEX IF NOT EXISTS idx_orders_customer_id ON orders(customer_id);
