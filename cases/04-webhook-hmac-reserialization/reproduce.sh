#!/usr/bin/env bash
# Case 04 reproduction: POST a JSON webhook payload with whitespace
# (what a real sender's JSON encoder emits by default), with the
# X-Signature computed over those exact raw bytes. The /webhook
# endpoint parses the body, re-serializes it without whitespace, hashes
# *that*, and rejects with `bad signature` because its hash is over
# different bytes than the sender's hash.
#
# Idempotent: seed/reset.sh runs first.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

./seed/reset.sh >/dev/null

# Payload with whitespace — i.e. what a real sender's JSON library
# (Python's stdlib `json.dumps`, JavaScript's `JSON.stringify`,
# Go's `encoding/json` with default settings, etc.) produces by
# default. The whitespace is the case's whole point: any byte
# transformation between sender and receiver invalidates the HMAC,
# and whitespace stripping is the most common transformation.
PAYLOAD='{"event": "order.placed", "id": 42}'
TS="$(date +%s)"

# Compute HMAC over the raw bytes from the sender's perspective.
# The signed string is `<timestamp>.<body>`, hashed with HMAC-SHA256,
# hex-encoded, prefixed `v1=`. Mirrors api/main.py::_compute_sig
# exactly. The `whsec_devonly` secret matches docker-compose.yml's
# WEBHOOK_SECRET env value — both must be identical for any
# signature scheme to work.
SIG="$(printf '%s.%s' "$TS" "$PAYLOAD" | \
    openssl dgst -sha256 -hmac "whsec_devonly" -hex | awk '{print "v1="$2}')"

# POST the payload + headers. --data-binary preserves the bytes
# exactly (--data alone strips newlines, which would change the body
# length and produce a different — but still wrong — failure mode,
# muddying the case).
OUT="$(curl -sS -o /tmp/case04-body.json -w 'http_code=%{http_code}\n' \
    -X POST \
    -H "Content-Type: application/json" \
    -H "X-Timestamp: ${TS}" \
    -H "X-Signature: ${SIG}" \
    --data-binary "${PAYLOAD}" \
    http://localhost:8000/webhook)"

BODY="$(cat /tmp/case04-body.json)"
echo "${OUT}"
echo "body=${BODY}"

# Capture the api log line. The `webhook=signature_mismatch` line
# carries the body_len field that is the case's smoking gun — the
# byte count the receiver hashed over (the re-serialized form, no
# whitespace) differs from what the sender claimed.
sleep 0.3
docker compose logs --tail=50 api | grep -E 'webhook=signature_mismatch|method=POST path=/webhook' \
    | tail -n 5 > cases/04-webhook-hmac-reserialization/logs.txt || true

# Final contract output for tests/test_reproductions.py.
echo "${OUT}" | sed 's/.*\(http_code=[0-9]*\).*/\1/'
echo "body=${BODY}"
