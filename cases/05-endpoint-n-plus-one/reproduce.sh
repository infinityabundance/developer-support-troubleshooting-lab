#!/usr/bin/env bash
# Case 05 reproduction: hit /orders?limit=200 (the broken N+1
# endpoint), parse the diag block from the response body, observe
# `queries=201` (the smoking gun: 1 + limit instead of the bounded 2
# the fix achieves at /orders/v2). Captures the api log line and an
# EXPLAIN ANALYZE of the per-row query for the evidence file.
#
# Idempotent: seed/reset.sh runs first. The orders fixture data is
# seeded by 001_init.sql at db boot (200 orders, 10 customers); reset
# does not need to re-seed it.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

./seed/reset.sh >/dev/null

# Hit /orders with limit=200, capture the response body to a tempfile
# so we can extract the diag block separately. Status code goes to
# stdout via `-w`; body to /tmp/case05-body.json via `-o`.
OUT="$(curl -sS -o /tmp/case05-body.json -w 'http_code=%{http_code}\n' \
    'http://localhost:8000/orders?limit=200')"

# Parse the diag block out of the JSON body. Two fields:
#   - queries: deterministic (depends only on `limit`, not on hardware)
#   - dur_ms:  hardware-dependent, bucketed into "fast" / "slow" with
#              a 100ms threshold. The bucket is captured for human
#              readability; the harness does not assert against it
#              (case 05's expected-output.txt only pins queries=201
#              with a regex anchor — see tests/test_reproductions.py
#              for the regex prefix syntax).
DIAG="$(python3 -c "
import json
b=json.load(open('/tmp/case05-body.json'))
d=b.get('diag', {})
print(f'queries={d.get(\"queries\")} dur_ms_bucket={\"slow\" if (d.get(\"dur_ms\") or 0) > 100 else \"fast\"}')
")"

echo "${OUT}"
echo "${DIAG}"

# Capture the api log line plus an EXPLAIN ANALYZE for the per-row
# customer lookup. The EXPLAIN is included to make a specific point
# in the case writeup: the per-query plan is FAST (PK index hit), so
# the bug is round-trip count, not query plan. A reader who sees
# 201 queries in the log might think "let me optimize that query" and
# go down the wrong rabbit hole; the EXPLAIN block in logs.txt
# pre-empts that.
sleep 0.3
{
    echo "# api log"
    docker compose logs --tail=50 api | grep -E 'endpoint=/orders' | tail -n 1
    echo
    echo "# EXPLAIN ANALYZE: per-row customer lookup"
    docker compose exec -T db psql -U app -d app -c \
        "EXPLAIN ANALYZE SELECT name FROM customers WHERE id = 1;" 2>/dev/null
} > cases/05-endpoint-n-plus-one/logs.txt || true

# Final contract output for tests/test_reproductions.py.
echo "${OUT}" | sed 's/.*\(http_code=[0-9]*\).*/\1/'
echo "${DIAG}"
