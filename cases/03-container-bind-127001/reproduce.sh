#!/usr/bin/env bash
# Case 03 reproduction: restart api bound to 127.0.0.1 inside the
# container, confirm the in-container healthcheck still passes (the
# process can talk to itself) but the from-host curl fails (the host
# can't reach the container's loopback). Captures `ss -tulpn` from
# inside the container as evidence.
#
# Idempotent: seed/reset.sh runs first; the script also restores the
# default 0.0.0.0 bind at the end so subsequent cases work. If this
# script is interrupted between the BIND_HOST=127.0.0.1 restart and
# the BIND_HOST=0.0.0.0 restore, the next reproduce.sh that needs the
# api will hang on the from-host healthcheck. Re-run reset.sh in that
# case.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

./seed/reset.sh >/dev/null

# Restart api with the container-loopback bind — this is the broken
# state the case demonstrates. docker compose interpolates ${BIND_HOST}
# from the shell env into the api service definition; setting it here
# overrides the compose default of 0.0.0.0.
BIND_HOST=127.0.0.1 docker compose up -d api >/dev/null

# Wait up to 10s (20 × 0.5s) for the new api process to start
# accepting connections on its container-internal loopback. Without
# this loop, the next docker compose exec can race against the still-
# starting uvicorn and return false negatives.
for _ in $(seq 1 20); do
    if docker compose exec -T api wget -qO- http://localhost:8000/healthz >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
done

# From inside the container: should succeed because the container's
# loopback IS where the app is bound. This is what makes the bug
# subtle in production — the in-container healthcheck (which docker
# compose itself runs) keeps returning healthy, hiding the bug from
# any monitoring that only looks at container health.
INSIDE_RC=0
docker compose exec -T api wget -qO- http://localhost:8000/healthz >/dev/null 2>&1 || INSIDE_RC=$?

# From the host: should fail because the host's loopback is NOT the
# container's loopback. --max-time 3 caps the curl in case of weird
# network states; `|| true` keeps `set -e` from killing the script
# on the expected non-zero exit. http_code=000 means "no HTTP response
# was received"; exitcode varies by container runtime (see the
# Runtime portability note in the case README).
HOST_OUT="$(curl -sS -o /dev/null -w 'http_code=%{http_code} exitcode=%{exitcode}' \
    --max-time 3 http://localhost:8000/healthz || true)"

# Capture `ss -tulpn` from inside the container as the evidence file.
# The case's README "Evidence" section quotes this output to show the
# `127.0.0.1:8000` listener — the smoking gun. The apt-get install of
# iproute2 is conditional because the api container doesn't ship with
# `ss` by default; on a fresh image this triggers a one-time install.
docker compose exec -T api sh -c \
    "command -v ss >/dev/null 2>&1 || (apt-get update -qq && apt-get install -y -qq iproute2 >/dev/null); \
     ss -tulpn 2>/dev/null | head -n 5" \
    > cases/03-container-bind-127001/logs.txt 2>&1 || true

# Final contract output for tests/test_reproductions.py.
echo "inside_container_healthz_rc=${INSIDE_RC}"
echo "${HOST_OUT}"

# CRITICAL: restore the default bind so subsequent reproductions and
# tests can reach the api from the host. If this line doesn't run
# (script interrupted, error before this point), the api stays bound
# to container loopback until the next `docker compose down -v` or
# explicit reset.
BIND_HOST=0.0.0.0 docker compose up -d api >/dev/null
