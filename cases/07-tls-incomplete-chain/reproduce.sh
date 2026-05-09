#!/usr/bin/env bash
# Case 07 reproduction: stand up a TLS server twice — once configured
# with a leaf-only cert (the original bug shape), once with leaf +
# intermediate (the fix). Curl each, capture the curl exit codes plus
# the openssl s_client chain output. Demonstrates that a client whose
# trust store has the root but not the intermediate fails verify
# against the leaf-only server (curl exit 60: unable to get local
# issuer certificate) and succeeds against the fullchain server.
#
# Self-contained: does NOT require the docker-compose stack. The TLS
# server is a tiny Python script (tls_server.py) and the cert chain is
# generated fresh in a tempdir on each run. Self-contained because TLS
# support tickets are about cert files and trust stores, not the
# application platform — keeping the case standalone makes the
# reproduction faster (no docker compose up) and clearer (no
# unrelated services running).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="$(mktemp -d -t case07-XXXXXX)"
PORT=18443  # Fixed port simplifies the reproduction; tests use unused_tcp_port.
LOG="${ROOT}/cases/07-tls-incomplete-chain/logs.txt"
SERVER_PY="${ROOT}/cases/07-tls-incomplete-chain/tls_server.py"

# Cleanup on exit (any exit, including errors). Kills any lingering
# server process and removes the tempdir of generated PEM material.
# Without this, a script interruption could leave an orphan TLS server
# bound to PORT, blocking the next reproduction with a port conflict.
cleanup() {
    if [[ -n "${SERVER_PID:-}" ]]; then
        kill -9 "${SERVER_PID}" 2>/dev/null || true
        wait "${SERVER_PID}" 2>/dev/null || true
    fi
    rm -rf "${WORK}"
}
trap cleanup EXIT

cd "${WORK}"

# ---------- 1. Generate a fresh 3-tier cert chain ----------
#
# Root CA -> Intermediate CA -> Leaf(CN=localhost). Days=1 because the
# certs only need to live as long as this script runs; longer validity
# windows make no difference here. Subjects are deliberately named
# `Test Root CA` etc. so a reader of the captured s_client output
# can tell at a glance these are throwaway certs.

# Root CA (self-signed, will be the only cert in the client's trust store).
openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
    -keyout root.key -out root.pem \
    -subj "/CN=Test Root CA" >/dev/null 2>&1

# Intermediate CA: CSR signed by the root, with basicConstraints=CA:TRUE
# so it can in turn sign the leaf. The extfile bash-process-substitution
# is the cleanest way to pass an inline x509 extension config without
# leaving a temp file around.
openssl req -newkey rsa:2048 -nodes \
    -keyout int.key -out int.csr \
    -subj "/CN=Test Intermediate CA" >/dev/null 2>&1
openssl x509 -req -in int.csr -CA root.pem -CAkey root.key -CAcreateserial \
    -days 1 -out int.pem \
    -extfile <(printf "basicConstraints=CA:TRUE\nkeyUsage=keyCertSign") \
    >/dev/null 2>&1

# Leaf: signed by the intermediate. CN=localhost + SAN=localhost,127.0.0.1
# so curl's hostname verification accepts both `https://localhost:PORT`
# and `https://127.0.0.1:PORT` against this cert.
openssl req -newkey rsa:2048 -nodes \
    -keyout leaf.key -out leaf.csr \
    -subj "/CN=localhost" >/dev/null 2>&1
openssl x509 -req -in leaf.csr -CA int.pem -CAkey int.key -CAcreateserial \
    -days 1 -out leaf.pem \
    -extfile <(printf "subjectAltName=DNS:localhost,IP:127.0.0.1") \
    >/dev/null 2>&1

# Two cert-files the server can present:
#   leaf.pem        - leaf only (the broken state). Single PEM block.
#   fullchain.pem   - leaf followed by intermediate (the fixed state).
#                     Two PEM blocks; the server sends both during
#                     handshake; the client uses the intermediate to
#                     bridge the leaf's chain up to the root it trusts.
cat leaf.pem int.pem > fullchain.pem

SERVER_PID=""

# ---------- start_server / stop_server / probe_curl / probe_chain ----------

start_server() {
    # $1 = cert file to load (leaf.pem or fullchain.pem). Single-cert
    # files require --allow-leaf-only because tls_server.py refuses to
    # start with an incomplete chain by default — that's the boot-time
    # safety gate the case-07 escalation proposes, lives in the server
    # itself so a config that would ship a broken chain fails fast.
    # The reproduction bypasses it on purpose to demonstrate the
    # original bug shape; production callers should not pass the flag.
    local cert="$1"
    local extra_args=()
    if [[ "${cert}" == "leaf.pem" ]]; then
        extra_args+=(--allow-leaf-only)
    fi
    python3 "${SERVER_PY}" "${cert}" leaf.key "${PORT}" "${extra_args[@]}" >/dev/null 2>&1 &
    SERVER_PID=$!
    # Wait for the listener (up to 5s = 50 × 0.1s). bash's /dev/tcp is
    # the cheapest "is the port accepting connections" probe — no
    # subprocess fork. The redirect-into-/dev/tcp opens and immediately
    # closes a connection.
    for _ in $(seq 1 50); do
        if (echo > /dev/tcp/127.0.0.1/"${PORT}") 2>/dev/null; then
            return 0
        fi
        sleep 0.1
    done
    return 1
}

stop_server() {
    # SIGKILL (kill -9) rather than SIGTERM because tls_server.py
    # blocks on serve_forever() with no signal handler installed; SIGTERM
    # would be ignored. Then wait until the port is actually released
    # so the next start_server has a clean port to bind.
    if [[ -n "${SERVER_PID:-}" ]]; then
        kill -9 "${SERVER_PID}" 2>/dev/null || true
        wait "${SERVER_PID}" 2>/dev/null || true
        SERVER_PID=""
    fi
    for _ in $(seq 1 30); do
        if ! (echo > /dev/tcp/127.0.0.1/"${PORT}") 2>/dev/null; then
            return 0
        fi
        sleep 0.1
    done
    return 0
}

probe_curl() {
    # Hit the TLS endpoint with --cacert root.pem (only the root in the
    # trust store; nothing else) and capture the curl exit code. The
    # `|| rc=$?` keeps `set -e` from killing the script on the expected
    # non-zero exit (60 against leaf-only).
    local rc=0
    curl -sS --cacert root.pem -o /dev/null -w "%{http_code}\n" \
        "https://localhost:${PORT}/" >/dev/null 2>&1 || rc=$?
    echo "${rc}"
}

probe_chain() {
    # Capture the certificate chain the server presents, plus the
    # verify result, into a normalized form the case README quotes.
    # awk filters to the lines that matter (depth, verify errors,
    # chain block subjects/issuers, final return code) and drops
    # the noise (full PEM blobs, extension dumps).
    {
        echo | openssl s_client -connect "localhost:${PORT}" \
                -showcerts -CAfile root.pem 2>&1 \
            | awk '
                /^depth=/                {print; next}
                /^verify error/          {print; next}
                /^Verify return code/    {print; next}
                /^Certificate chain/     {print; next}
                /^[[:space:]]+[0-9]+ s:/ {print; next}
                /^[[:space:]]+i:/        {print; next}
              ' \
            | head -n 30
    } || true
}

# ---------- 2. BEFORE FIX: leaf only ----------
#
# Each phase runs the server twice: once for the chain probe (s_client
# captures the chain into logs.txt), once for the curl probe (which
# captures the exit code into the contract output). Restarting between
# probes keeps each probe isolated to its own server process — avoids
# any state from a previous probe affecting the next.

> "${LOG}"
{
    echo "=== BEFORE FIX: server presents leaf only (cert=leaf.pem) ==="
    start_server leaf.pem; probe_chain; stop_server
} >> "${LOG}"

start_server leaf.pem
before_fix_exit="$(probe_curl)"
stop_server

# ---------- 3. AFTER FIX: leaf + intermediate ----------

{
    echo
    echo "=== AFTER FIX: server presents full chain (cert=fullchain.pem) ==="
    start_server fullchain.pem; probe_chain; stop_server
} >> "${LOG}"

start_server fullchain.pem
after_fix_exit="$(probe_curl)"
stop_server

# ---------- 4. Contract output for tests/test_reproductions.py ----------
echo "before_fix_exit=${before_fix_exit}"  # Expected: 60 (unable to get local issuer certificate)
echo "after_fix_exit=${after_fix_exit}"    # Expected: 0  (HTTP 200 returned, chain validated)
