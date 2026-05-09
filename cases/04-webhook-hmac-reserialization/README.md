# Case 04 — Webhook HMAC fails after JSON re-serialization

## Symptom (as reported)

> Customer ticket #4561, P1.
> "Our webhook receiver is rejecting every event with `bad signature`. The integration was working yesterday. We have not rotated the webhook secret. Sender-side logs show we computed the HMAC over the exact body we sent. Why is your service computing a different HMAC?"

## Reproduction

```bash
./reproduce.sh
```

The script signs a payload (timestamp + raw bytes) with the configured secret, posts it to `/webhook` with the correct `X-Signature` and `X-Timestamp` headers, and gets back 401 `bad signature`.

## Diagnostic narrative — including the wrong first hypothesis

**Hypothesis 1 (wrong): clock skew on `X-Timestamp`.** The signature scheme includes the timestamp. If the receiver reconstructs the signed string with a normalized timestamp, a sender that drifted by even a second would get rejected. This was the first guess because it explains "worked yesterday, fails today" cleanly.

Disproved cheaply: re-ran the reproduction with the sender's exact timestamp baked in by the receiver before computing the expected signature. Still mismatched. Not a clock issue.

**Hypothesis 2 (wrong): wrong secret on the receiver.** Re-checked `WEBHOOK_SECRET` env var, computed the expected signature manually with `python -c "import hmac, hashlib; ..."` over the raw bytes the sender said they used. The manual computation matched the *sender's* signature exactly. So the receiver's secret is correct; the receiver is signing different bytes.

**Hypothesis 3 (right): the receiver is verifying over re-serialized JSON.** Looked at the verifier middleware. It calls `await request.json()` *before* the HMAC check. FastAPI consumes the request body to do that. The verifier then re-serializes the parsed object via `json.dumps(parsed, separators=(",", ":"))` and HMACs that. The sender HMAC'd the raw bytes. The two byte sequences are not identical — `json.dumps` reorders keys, normalizes whitespace, and `separators=(",", ":")` is *not* how the sender encoded their payload (their library leaves spaces after `:` and `,`). The two byte-streams differ by single-character whitespace and key ordering, which is enough to break HMAC.

The smoking gun is in the log line: `body_len=` differs between what the sender claimed they sent and what the receiver verified over.

## Evidence

`logs.txt`:

```
webhook=signature_mismatch expected_prefix=v1=8a3c... got_prefix=v1=2d91... body_len=63
```

Sender claims `body_len=71`. Receiver computed over 63 bytes. That eight-byte delta is whitespace.

## Root cause

`api/main.py::webhook` reads and parses the JSON body before the HMAC check. The HMAC is then computed over `json.dumps(parsed, separators=(",", ":"))`, which is not byte-identical to the original payload. Senders that included whitespace, used a different key order, or used a JSON library with different defaults will all fail verification.

This is the canonical "verify over raw bytes" rule from every webhook spec (Stripe, GitHub, Slack). It is a one-line discipline that gets violated constantly because frameworks make body parsing convenient and `request.body()` slightly less so.

## Fix

**Workaround:** none on the customer side that doesn't compromise security. They cannot reasonably be asked to match our JSON re-serialization byte-for-byte.

**Proper fix (this is the one that ships):** capture the raw body before any parsing, verify HMAC over the raw bytes, then parse. Pseudocode:

```python
body_raw = await request.body()
expected = _compute_sig(WEBHOOK_SECRET, x_timestamp, body_raw)
if not hmac.compare_digest(expected, x_signature):
    raise HTTPException(401, "bad signature")
parsed = json.loads(body_raw)  # only after the signature is verified
```

The fixed implementation lives at `/webhook/v2` in `api/main.py`, alongside the still-broken `/webhook` so this case's reproduction script keeps demonstrating the original bug shape. The rule to teach in onboarding: **verify before parse, always, for any signed payload.**

## Outcome

POSTing the same whitespace-bearing payload and over-raw-bytes signature to `/webhook/v2` returns 200. The pinning tests in `tests/test_webhook.py` (`test_webhook_v2_accepts_signature_over_raw_bytes_with_whitespace` plus the symmetry-break `test_webhook_v2_rejects_when_signature_computed_over_reserialized`) fail immediately if a future refactor reverts `/webhook/v2` to parse-then-reserialize-then-hash; verified by deliberate revert experiment.

## Tracing a single request end-to-end

Once the platform is up, every request is tagged with an `rid` propagated through the api logs. Reproduce a failing webhook, copy the `rid` out of the response header, then:

```
$ make trace REQUEST=case04-demo-1700000000
[api] <ts> WARNING api rid=case04-demo-1700000000 webhook=signature_mismatch expected_prefix=v1=e687... got_prefix=v1=e0c5... body_len=65
[api] <ts> INFO    api rid=case04-demo-1700000000 method=POST path=/webhook status=401 dur_ms=0.4
```

Two lines tell the whole story: the `body_len=65` is the receiver's re-serialized form (5 bytes shorter than the sender's 71-byte payload), and the response is 401 within 0.4 ms because the divergence is caught at the HMAC step. `make trace` works against any rid in the recent log buffer; for cases that exercise the database it surfaces correlated db lines from the same time window. It's the bridge from "I can read one log line" to "I can read the full request path."

## Adjacent failure modes (not hit in this case, but the same pattern)

- **Slack-style JSON-with-leading-bytes.** If the receiver decodes UTF-8 with BOM stripping before HMAC, the byte stream the verifier checks is two bytes shorter than the bytes the sender signed. Same root cause class, different layer.
- **multipart/form-data webhooks** where the receiver canonicalizes the boundary string. The verifier sees a normalized boundary, the sender HMAC'd the original. Common in older PagerDuty / Twilio flows.
- **Compressed payloads.** Sender HMACs the raw body; reverse proxy (nginx with `gzip on` for upstream responses or `gunzip` on requests) decompresses before the verifier sees it. Verifier hashes uncompressed bytes; sender hashed compressed bytes. Looks identical to this case from the receiver's logs.

The unifying rule across all four (this case + three adjacents): if anything between the wire and the HMAC step touches the bytes — parsing, BOM-stripping, decompression, boundary canonicalization — the HMAC is now over a different artifact than the sender signed.
