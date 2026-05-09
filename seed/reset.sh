#!/usr/bin/env bash
# Return the platform to a known-good baseline:
#   - migration 001 applied (auto-loaded by the postgres image's initdb hook)
#   - migration 002 NOT applied (the case-02 broken state)
#   - schema_migrations holds version 1 only; rows for higher versions deleted
#   - audit_log dropped (case 02 fixture); idx_orders_customer_id dropped (case 05 fixture)
#   - api binding to 0.0.0.0 (case-03 default-good state, restored after the
#     case 03 reproduction which flips BIND_HOST to 127.0.0.1)
#   - JWT_AUDIENCES env carries the compose-baked default ("api"); the
#     legacy JWT_AUDIENCE env var still works as a fallback for callers
#     that haven't migrated to the list form
# Idempotent. Safe to call before every reproduce.sh and before every
# pinning test that needs the live stack in a clean state.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Drop and re-create the db objects that individual cases mutate.
# DROP IF EXISTS makes this idempotent — running reset on a clean
# baseline succeeds without error. CASCADE on audit_log because future
# migrations might add foreign keys pointing to it; the cascade keeps
# us from having to enumerate dependencies.
docker compose exec -T db psql -U app -d app -c "DROP TABLE IF EXISTS audit_log CASCADE;" >/dev/null
docker compose exec -T db psql -U app -d app -c "DROP INDEX IF EXISTS idx_orders_customer_id;" >/dev/null

# Roll the schema-migrations registry back to bootstrap-only so case 02
# can reproduce the "version 2 was never applied here" state on the next
# run. Without this, a previous reproduction that called /admin/migrate/2
# would have inserted version 2 into the registry, and the next case 02
# reproduce would observe schema_migrations already at v2 — the bug the
# case demonstrates would no longer fire.
#
# The `2>/dev/null || true` fallback covers stacks brought up before
# schema_migrations existed in 001_init.sql (i.e. an older version of
# the lab cached in someone's docker volumes); on those, the table
# doesn't exist and the DELETE errors. We don't care — the next
# `docker compose up -d` against the current 001 will create the table.
docker compose exec -T db psql -U app -d app -c "DELETE FROM schema_migrations WHERE version > 1;" >/dev/null 2>&1 || true

# Defensive cleanup: clear any per-case env-var overrides that an
# interrupted reproduction might have left in the shell. The two named
# vars below are historical — current reproductions inline their
# overrides on the docker compose command line rather than exporting
# to the shell — but the cleanup costs nothing and protects against
# a future case that does export an override.
if [ -n "${BIND_HOST_OVERRIDE:-}" ] || [ -n "${JWT_AUDIENCE_OVERRIDE:-}" ]; then
    unset BIND_HOST_OVERRIDE JWT_AUDIENCE_OVERRIDE
fi

# Restart api with the default 0.0.0.0 bind. Critical: case 03's
# reproduction flips BIND_HOST to 127.0.0.1; if its cleanup-restore
# didn't run (script interrupted), the api stays bound to the
# container's loopback and every subsequent reproduce.sh that needs to
# hit it from the host hangs. Restarting here unconditionally makes
# the baseline robust against that.
BIND_HOST=0.0.0.0 docker compose up -d api >/dev/null

# Wait up to 30s for the api healthcheck to come green. Slow path:
# 30s × 1s = 30s. Without this loop, a too-fast reproduce.sh after
# reset.sh can race the api's startup and observe a connection refused
# from the host. If the api never goes healthy in 30s, exit 1 with a
# clear message — something is wrong with the platform itself, not
# with any individual case.
for _ in $(seq 1 30); do
    if curl -sf http://localhost:8000/healthz >/dev/null; then
        echo "[reset] platform baseline ready"
        exit 0
    fi
    sleep 1
done
echo "[reset] api did not become healthy" >&2
exit 1
