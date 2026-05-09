# Escalation: JWT_AUDIENCE config drift between env and verifier

**Severity:** P3 (single-tenant impact, customer has a one-line workaround)
**Component:** `api/main.py::me` — `jwt.decode(..., audience=JWT_AUDIENCE)`
**Triggering ticket:** #4471

## What happened

Verifier deployed to staging with `JWT_AUDIENCE=api`. Customer's IdP issues `aud="api-staging"` for the staging environment. `pyjwt` raises `InvalidAudienceError`, we return 401. No code defect; the verifier is doing exactly what its config says.

## Why this got past review

An instance of the symmetry trap (see *Why configuration drift escapes tests* in `docs/support-casebook.md`). The verifier was tested against tokens minted by our own test suite, which read `JWT_AUDIENCE` from the same env file the verifier read. Test and production code shared a source of truth, so the test could not disagree with the production config — every test passed regardless of what value the env carried. The test was answering "does the verifier accept tokens we sign with the configured audience?" when the question that matters in production is "does the verifier accept tokens our customers sign with the audience their IdP issues?" Those are different questions and we never wrote a test for the second one.

## Why this will recur

`JWT_AUDIENCE` is a single string. We have at least two tenants on `api-staging`, at least one using their tenant ID as `aud`, and likely more we haven't heard from yet. Each one files the same ticket the first time they connect.

## Proposed fix

`pyjwt`'s `audience=` accepts a list. Change `JWT_AUDIENCE` (string) to `JWT_AUDIENCES` (comma-separated) and split on read:

```python
audiences = [a.strip() for a in os.environ["JWT_AUDIENCES"].split(",") if a.strip()]
jwt.decode(token, JWT_SECRET, algorithms=["HS256"], audience=audiences)
```

Ship alongside the old var for one release with a deprecation warning, then drop.

## Pinning test that prevents recurrence

`tests/test_auth.py::test_audience_not_in_configured_list_is_rejected` — explicitly mint a token with an audience value the test config does NOT name in `JWT_AUDIENCES`, then a second token with one that IS named (`test_audience_in_configured_list_is_accepted`), then assert the first 401s and the second passes. The point is to break the test-mint / prod-verify symmetry: the test no longer signs only with values from the verifier's config. If a future refactor reverts the change, this test fails immediately.

## What this does not solve

A tenant putting an arbitrary string in `aud` (e.g. their own tenant ID) still requires a config push on our side. The longer-term fix is per-tenant audience in the tenant settings table, validated at admission. Out of scope for this ticket; file separately.
