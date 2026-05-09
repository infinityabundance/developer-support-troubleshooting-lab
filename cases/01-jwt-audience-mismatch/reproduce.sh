#!/usr/bin/env bash
# Case 01 reproduction: mint a JWT with the wrong audience claim, hit
# /me, observe the 401 invalid_audience response and the corresponding
# log line. Captures real api log output into logs.txt.
#
# Idempotent: seed/reset.sh runs first to put the stack in a known
# state; the script can be run repeatedly.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

# Reset to baseline before exercising the case. Without this, state
# accumulated by previous reproductions (audit_log dropped/recreated,
# api restarted on a different bind, schema_migrations carrying
# version 2, etc.) could mask or change this case's behavior.
./seed/reset.sh >/dev/null

# Mint a token with the WRONG audience claim using the same secret the
# api uses. Done in-container via `docker compose exec api python` to
# avoid requiring pyjwt on the host — keeps the script runnable on a
# fresh machine that only has docker installed. The token's `aud`
# claim is "api-staging"; the verifier (per docker-compose.yml)
# accepts "api". The signature is valid; only the audience claim is
# wrong, which is the case's whole point.
TOKEN="$(docker compose exec -T api python - <<'PY'
import jwt, os
print(jwt.encode(
    {"sub": "user-42", "aud": "api-staging"},
    os.environ["JWT_SECRET"],
    algorithm="HS256",
))
PY
)"
# Strip the trailing CR/LF the heredoc + container roundtrip adds.
# Without this strip the curl's Authorization header would carry a
# trailing newline, which some HTTP clients tolerate and some don't —
# making the case nondeterministically flaky.
TOKEN="$(echo "$TOKEN" | tr -d '\r\n')"

# curl with -w writes the http_code to stdout; -o sends the body to a
# file so we can echo it on a separate line for the diff. -sS = silent
# but show errors (so a connection failure surfaces, but a 4xx response
# body doesn't pollute stdout with curl's progress meter).
OUT="$(curl -sS -o /tmp/case01-body.json -w 'http_code=%{http_code}\n' \
    -H "Authorization: Bearer $TOKEN" \
    http://localhost:8000/me)"

BODY="$(cat /tmp/case01-body.json)"
echo "${OUT}"
echo "body=${BODY}"

# Capture the api log line for this request so logs.txt is real
# evidence (not a hand-authored sample). The 0.3s sleep gives the
# log shipper a moment to flush; without it, the grep can run before
# the line lands and logs.txt comes out empty.
sleep 0.3
docker compose logs --tail=50 api | grep -E 'auth=invalid_audience|method=GET path=/me' \
    | tail -n 5 > cases/01-jwt-audience-mismatch/logs.txt || true

# Final contract output for tests/test_reproductions.py — these are the
# lines the harness substring-matches against expected-output.txt.
# Normalized to strip per-run noise (rid, timestamps, dur_ms) so the
# diff is stable across runs and machines.
echo "${OUT}" | sed 's/.*\(http_code=[0-9]*\).*/\1/'
echo "body=${BODY}" | tr -d '\n'
echo
