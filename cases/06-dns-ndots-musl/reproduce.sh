#!/usr/bin/env bash
# Case 06 reproduction: from inside the Alpine sidecar (musl libc),
# resolve the `api` service name via two paths — `getent hosts api`
# (musl resolver path) and `dig api` (full DNS lookup) — for both
# the short name and the trailing-dot FQDN. Captures resolv.conf and
# both lookup paths' output as the case's evidence.
#
# Idempotent: seed/reset.sh runs first; the alpine sidecar is brought
# up if not already running; bind-tools (for dig) is apk-add'd
# conditionally so re-runs are fast.
#
# Runtime portability note: this case demonstrates a Docker-specific
# bug (embedded DNS at 127.0.0.11 + ndots:0 + musl). On Podman, the
# bug doesn't reproduce because Podman uses aardvark-dns at the
# network gateway with different ndots behavior. The contract test
# asserts only the runtime-agnostic fact (FQDN-with-trailing-dot
# resolves), not the Docker-specific signal. See the case README's
# "Runtime notes" section.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

./seed/reset.sh >/dev/null

# Make sure the alpine sidecar is up. The sidecar exists in
# docker-compose.yml as a long-lived `sleep infinity` container so
# we have a musl-libc environment to exec into without spinning up
# a fresh container per run.
docker compose up -d alpine-resolver >/dev/null
sleep 1

# Install dig (and drill, via bind-tools) in the sidecar. Idempotent:
# the `command -v dig` check skips the apk install on subsequent
# runs. apk-add is fast (10MB-ish package) but not free, so the guard
# matters when running reproduce-all in a loop.
docker compose exec -T alpine-resolver sh -c \
    'command -v dig >/dev/null 2>&1 || apk add --no-cache bind-tools >/dev/null'

# Capture the side-by-side getent vs dig output against `api` (short)
# and `api.` (FQDN, trailing dot). This is the evidence file the case
# README points at: a reader can see immediately which path resolves
# under which conditions. The `|| echo NXDOMAIN_or_empty` keeps a
# failed lookup from killing the script; we want the failure shape
# captured in logs.txt, not the script aborted.
{
    echo "# /etc/resolv.conf"
    docker compose exec -T alpine-resolver cat /etc/resolv.conf
    echo
    echo "# getent hosts api (short)"
    docker compose exec -T alpine-resolver sh -c "getent hosts api || echo NXDOMAIN_or_empty"
    echo
    echo "# getent hosts api. (FQDN)"
    docker compose exec -T alpine-resolver sh -c "getent hosts api. || echo NXDOMAIN_or_empty"
    echo
    echo "# dig api +short"
    docker compose exec -T alpine-resolver dig api +short || true
    echo
    echo "# dig api. +short"
    docker compose exec -T alpine-resolver dig api. +short || true
} > cases/06-dns-ndots-musl/logs.txt

# Compute the contract metrics for the diff. Three numbers:
#   - resolv_conf_has_127_0_0_11: 1 on Docker (embedded DNS), 0 on
#     Podman (network-level resolver). Captured for diagnostic
#     value but NOT asserted by the test (would flake across runtimes).
#   - getent_short_lines: 1 if musl's resolver returned an answer
#     for the short name, 0 otherwise. Indicative; varies by runtime.
#   - getent_fqdn_lines: 1 if the FQDN form resolved. This IS the
#     deterministic teaching point that the harness asserts —
#     trailing-dot FQDN works reliably across runtimes.
RESOLV="$(docker compose exec -T alpine-resolver cat /etc/resolv.conf | tr '\n' ';' | head -c 80)"
GETENT_SHORT="$(docker compose exec -T alpine-resolver sh -c 'getent hosts api 2>/dev/null | wc -l' | tr -d ' \r\n')"
GETENT_FQDN="$(docker compose exec -T alpine-resolver sh -c 'getent hosts api. 2>/dev/null | wc -l' | tr -d ' \r\n')"

echo "resolv_conf_has_127_0_0_11=$(echo "$RESOLV" | grep -c '127.0.0.11')"
echo "getent_short_lines=${GETENT_SHORT}"
echo "getent_fqdn_lines=${GETENT_FQDN}"
