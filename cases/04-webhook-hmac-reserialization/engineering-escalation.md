# Escalation: webhook verifier signs re-serialized JSON instead of raw bytes

**Severity:** P1 (every webhook from every customer is rejected when payloads contain whitespace; affects all integrations)
**Component:** `api/main.py::webhook`
**Triggering ticket:** #4561

## What happened

The webhook handler calls `await request.json()` before computing the HMAC. The HMAC is then computed over `json.dumps(parsed, separators=(",", ":"))`, which is not byte-identical to the request body for any sender that uses a different JSON encoding. The sender computes HMAC over the raw bytes; the receiver computes over re-serialized bytes; they diverge.

This is a classic webhook verifier defect — Stripe, GitHub, Slack, etc. all explicitly document "verify over raw bytes" because of exactly this failure mode.

## Why this got past review

A textbook instance of the symmetry trap (see *Why configuration drift escapes tests* in `docs/support-casebook.md`). The handler was written to be ergonomic — `parsed` was already in scope, so the original author reused it. The test suite signed payloads using the same `json.dumps(..., separators=(",", ":"))` call as the verifier, so the tests passed. The tests were measuring "does the verifier agree with itself" rather than "does the verifier agree with a sender." The single-byte fix is the pinning test below; it signs raw bytes the verifier never sees during normal test runs, so the symmetry is broken.

## Proposed fix

```python
@app.post("/webhook")
async def webhook(request: Request, ...):
    body_raw = await request.body()
    expected = _compute_sig(WEBHOOK_SECRET, x_timestamp, body_raw)
    if not hmac.compare_digest(expected, x_signature):
        raise HTTPException(401, "bad signature")
    parsed = json.loads(body_raw)
    ...
```

Critical: `request.body()` must be called once and the bytes reused. Calling it after `request.json()` returns empty (stream consumed). The fixed implementation lives at `/webhook/v2` in `api/main.py`, alongside the still-broken `/webhook` so the case's reproduction script keeps demonstrating the original bug shape.

## Test changes

Two tests:

1. `test_webhook_v2_accepts_signature_over_raw_bytes_with_whitespace` — payload with deliberate whitespace `{"event": "x", "n": 1}`, signature computed over that exact byte sequence, posted to `/webhook/v2` (the fixed endpoint, alongside the still-broken `/webhook` that case 04 reproduces against). Must pass.
2. `test_webhook_v2_rejects_when_signature_computed_over_reserialized` — sign over `json.dumps(parsed, separators=(",", ":"))`, send the original whitespace-bearing raw bytes, must 401. Pins the bug closed: if anyone later "optimizes" `/webhook/v2` back into parsing first, this test catches it.
3. `test_webhook_v2_rejects_missing_signature_headers` — defensive: a request without `X-Signature` returns 400, not a crash.

## What this does not solve

Replay attacks: this case is purely about the signature path. Replay protection (rejecting timestamps older than N seconds, dedup on event ID) is a separate concern; existing replay handling stays unchanged.
