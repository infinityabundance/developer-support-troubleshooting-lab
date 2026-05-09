#!/usr/bin/env bash
# Case 02 reproduction: hit /audit against a db that has 001 applied
# but not 002, observe the 500 with `relation "audit_log" does not exist`
# and the corresponding api log line.
#
# Idempotent: seed/reset.sh runs first to put the stack in the case-02
# broken state (audit_log dropped, schema_migrations rolled back to
# version 1 only).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

./seed/reset.sh >/dev/null

# Confirm 002 is NOT applied. Captured to /tmp/case02-dt.txt for
# anyone running this interactively who wants to inspect the schema
# state; not asserted against by the harness.
docker compose exec -T db psql -U app -d app -c "\dt" | tee /tmp/case02-dt.txt >/dev/null

# Hit /audit. The handler tries `SELECT ... FROM audit_log`, psycopg
# raises UndefinedTable, the api catches and returns 500 with the
# error message in the body.
OUT="$(curl -sS -o /tmp/case02-body.json -w 'http_code=%{http_code}\n' \
    http://localhost:8000/audit)"

BODY="$(cat /tmp/case02-body.json)"
echo "${OUT}"
echo "body=${BODY}"

# Capture the api log line. The 0.3s sleep gives the log shipper a
# moment to flush; without it the grep can run before the line lands.
sleep 0.3
docker compose logs --tail=50 api | grep -E 'db=undefined_table|method=GET path=/audit' \
    | tail -n 5 > cases/02-postgres-missing-relation/logs.txt || true

# Final contract output for tests/test_reproductions.py.
# The body sed strips psycopg's multiline LINE 1: ... ^ context that
# follows the "relation does not exist" message, so the assertion is
# stable across psycopg versions whose error formatting may differ.
echo "${OUT}" | sed 's/.*\(http_code=[0-9]*\).*/\1/'
echo "body=${BODY}" | sed 's/relation \\"audit_log\\" does not exist.*/relation "audit_log" does not exist/'
