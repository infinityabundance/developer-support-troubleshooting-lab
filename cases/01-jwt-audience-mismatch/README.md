# Case 01 — 401 from JWT audience-claim mismatch

## Symptom (as reported)

> Customer ticket #4471, P2.
> "Our staging integration started returning `401 invalid audience` from `/me` this morning. Nothing on our end changed. The token is signed with the same secret, hasn't expired, and works against your dev environment. Please investigate."

## Reproduction

```bash
./reproduce.sh
```

The script mints a token with `aud="api-staging"` and calls `/me`. The API verifies against `aud="api"` and returns 401.

## Diagnostic narrative

First test: was it TLS, network, or the app? `curl -v` showed the TCP+TLS handshake completing and a 401 body coming back from the application — not from a proxy, not from the framework. So the rejection is intentional and originates in the auth code path.

Second test: is the signature valid? Decoded the token without verification (`jwt.decode(token, options={"verify_signature": False})`) and re-encoded with the same secret. Signature matched. Signature is fine.

Third test: read the claims. `aud="api-staging"`. The verifier is configured with `JWT_AUDIENCE=api`. That is the bug.

Disproven hypothesis on the way: assumed first that the customer had rotated their secret. Disproven by re-encoding with the dev secret and seeing a byte-identical signature. Five minutes saved by checking signature before claims.

## Evidence

`logs.txt` shows the API line:

```
auth=invalid_audience expected=['api'] err=Audience doesn't match
```

The `expected=` field on the log line is what makes this case 30 seconds, not 30 minutes. Without it on the log line, every JWT 401 ticket starts with "what audience is this verifier expecting?"

## Root cause

The customer's identity provider was reconfigured to issue tokens with `aud="api-staging"` for the staging environment. The verifier in this service was deployed with `JWT_AUDIENCE=api` for both environments. The mismatch is environmental config, not a code defect on either side.

## Fix

Two paths.

**Workaround (customer side, today):** the customer can override the audience claim in their token-mint call back to `aud="api"`. One-line config change on their end. This is what `customer-response.md` proposes.

**Proper fix (engineering, this sprint):** make the verifier accept a list of audiences and ship a per-environment override. The verifier already supports a list — `jwt.decode(..., audience=[...])` — so this is mostly a config change. Tracked in `engineering-escalation.md`.

## Outcome

The fix is shipped in `api/main.py`: the verifier reads `JWT_AUDIENCES` (comma-separated), with the legacy single-string `JWT_AUDIENCE` honored as a fallback so this case's reproduction script still demonstrates the original bug shape. The customer-side workaround unblocks immediately; the engineering fix means the next tenant on a different audience is one env-var-list update instead of a config push. Pinned by `tests/test_auth.py::test_audience_not_in_configured_list_is_rejected` and its companion accept-list tests; the symmetry break (test mints with audiences the verifier cannot see in any production config) prevents the original bug from re-shipping.

## Adjacent failure modes (not hit in this case, but the same pattern)

- **`iss` (issuer) mismatch.** Verifier configured with one issuer string; tenant's IdP issues tokens with a slightly different issuer URL (trailing slash, `https://` vs scheme-relative, region-prefixed subdomain). pyjwt's `InvalidIssuerError` produces the same 401 shape and the same surprised-customer ticket. Same diagnostic move applies: log the *expected* issuer alongside the *received* one.
- **`kid` pointing at a key not in the JWKS cache.** Tenant rotates their signing key; the verifier's JWKS fetcher hasn't refreshed. Token is well-formed and the customer's IdP says "yes, that's our token," but verification can't find the key. Subtler than aud/iss because nothing about the token is wrong — the symptom is `KeyError` deep in the verifier path. Fix: shorten the JWKS cache TTL and add a forced re-fetch on `kid` cache miss.
- **`aud` is a JSON array in the token but the verifier treats it as a string.** RFC 7519 allows `aud` to be either a string or an array of strings. Custom verifiers that do `claims["aud"] == JWT_AUDIENCE` fail closed when `claims["aud"]` is `["api", "api-staging"]`. The pyjwt library handles this correctly; hand-rolled verifiers in microservice-mesh sidecars often do not. The bug only surfaces when a tenant configures multi-audience tokens.
