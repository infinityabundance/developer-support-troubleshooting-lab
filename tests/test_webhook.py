"""Pinning tests for case 04 — `/webhook/v2` verifies HMAC over raw
request bytes. Requires the docker-compose stack to be up."""
from __future__ import annotations

import hashlib
import hmac
import json
import socket

import httpx
import pytest

API_HOST = "127.0.0.1"
API_PORT = 8000
BASE = f"http://{API_HOST}:{API_PORT}"

# Must match docker-compose.yml's WEBHOOK_SECRET. Hardcoded here on purpose:
# if the env-var name changes (e.g. someone renames it to WEBHOOK_SECRETS),
# the test fails with a clear-signed-bytes mismatch instead of silently
# reading the new env. That's the symmetry break — test does not derive
# from the same env the production code reads.
WEBHOOK_SECRET = "whsec_devonly"


def _stack_is_up(host: str = API_HOST, port: int = API_PORT, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _stack_is_up(),
    reason="docker-compose stack not up; run `make up` first",
)


def _sign(secret: str, ts: str, body_bytes: bytes) -> str:
    """Mirror api/main.py::_compute_sig. Hash is over <ts> + "." + <body>."""
    mac = hmac.new(secret.encode(), digestmod=hashlib.sha256)
    mac.update(ts.encode())
    mac.update(b".")
    mac.update(body_bytes)
    return "v1=" + mac.hexdigest()


def test_webhook_v2_accepts_signature_over_raw_bytes_with_whitespace():
    """Sign + send the same whitespace-bearing bytes; verifier must accept."""
    payload_with_whitespace = json.dumps(
        {"event": "x", "n": 1}, separators=(", ", ": ")
    ).encode()
    ts = "1700000000"
    sig = _sign(WEBHOOK_SECRET, ts, payload_with_whitespace)

    r = httpx.post(
        f"{BASE}/webhook/v2",
        content=payload_with_whitespace,
        headers={
            "Content-Type": "application/json",
            "X-Signature": sig,
            "X-Timestamp": ts,
        },
        timeout=5.0,
    )
    assert r.status_code == 200, (
        f"webhook/v2 rejected a signature computed over the exact bytes sent. "
        f"status={r.status_code}, body={r.text!r}, "
        f"body_len_sent={len(payload_with_whitespace)}"
    )
    assert r.json() == {"ok": True}


def test_webhook_v2_rejects_when_signature_computed_over_reserialized():
    """Sign re-serialized form, send original bytes; verifier must 401.
    This is the regression a parse-then-hash revert produces; this test
    flips from 401 to 200 in that case."""
    payload_with_whitespace = json.dumps(
        {"event": "x", "n": 1}, separators=(", ", ": ")
    ).encode()
    parsed = json.loads(payload_with_whitespace)
    payload_reserialized = json.dumps(parsed, separators=(",", ":")).encode()
    ts = "1700000000"
    # Sign the re-serialized (no-whitespace) form ...
    bogus_sig = _sign(WEBHOOK_SECRET, ts, payload_reserialized)

    # ... but send the ORIGINAL whitespace-bearing bytes.
    r = httpx.post(
        f"{BASE}/webhook/v2",
        content=payload_with_whitespace,
        headers={
            "Content-Type": "application/json",
            "X-Signature": bogus_sig,
            "X-Timestamp": ts,
        },
        timeout=5.0,
    )
    assert r.status_code == 401, (
        f"webhook/v2 accepted a signature computed over re-serialized bytes "
        f"that do not match the bytes on the wire. status={r.status_code}, "
        f"body={r.text!r}. The verifier may have regressed to parse-then-hash."
    )
    assert "bad signature" in r.text.lower()


def test_webhook_v2_rejects_missing_signature_headers():
    """No X-Signature header → 400, not a 5xx crash."""
    payload = b'{"event":"x"}'
    r = httpx.post(
        f"{BASE}/webhook/v2",
        content=payload,
        headers={"Content-Type": "application/json"},
        timeout=5.0,
    )
    assert r.status_code == 400, r.text
