# Escalation: reference image lets `BIND_HOST=127.0.0.1` ship without warning

**Severity:** P3 (configuration, customer-recoverable, but high recurrence)
**Component:** `api/Dockerfile` CMD; uvicorn invocation
**Triggering ticket:** #4533

## What happened

Customer set `BIND_HOST=127.0.0.1` (analogue of inheriting a localhost default from a Procfile or local-dev shell script). The application started, the in-container healthcheck passed, the published port was unreachable from the host. The container is doing exactly what it was told. The problem is that what it was told is silently wrong.

## Why this got past review

The Dockerfile accepts `BIND_HOST` from the environment with no validation and no startup log of the resolved value. There is no signal — at build time, at deploy time, or at runtime — that the configured bind is incompatible with how the container is being reached. The in-container healthcheck uses the container's own loopback, so it succeeds against any bind including the broken one. The healthcheck is answering "can the process talk to itself" when the question that matters is "can a request from outside the container reach this process."

This is the same shape as case 01's audience mismatch: the verifier-in-isolation passes, but the verifier-as-part-of-a-system fails, and the test is the verifier-in-isolation.

## Why this will recur

Filed once per quarter, different customer each time, same root cause. Every customer self-onboarding onto the reference platform that copies a `127.0.0.1` default forward will hit this. The mitigation has been "we explain the fix in the response email" which closes the ticket but doesn't reduce the rate.

## Proposed fix (image-side, two changes)

1. **Default `BIND_HOST` to `0.0.0.0` in the reference Dockerfile.** A user has to actively override to get the broken behaviour. This repo's Dockerfile already does this; the same change should land in the customer-shipped image.
2. **Emit `bound=<host>:<port>` at INFO on startup.** Right now the bind is invisible until something fails. A startup log line turns the next instance of this ticket from "the customer's port is published but unreachable, take 30 minutes to diagnose" into "grep startup logs for `bound=127.`, refer them to docs."

## Pinning test that prevents recurrence

`tests/test_bind.py::test_host_can_reach_default_bind_via_published_port` — boot the api with no `BIND_HOST` override, hit `http://localhost:<published-port>/healthz` from *outside* the container, expect 200. This explicitly tests the from-outside path that the in-container healthcheck cannot. If a future change sets the default back to `127.0.0.1` or breaks the publish wiring, this test fails immediately.

A static companion check `tests/test_bind.py::test_default_bind_host_is_zero_in_compose` parses `docker-compose.yml` and asserts the default value of the `BIND_HOST` interpolation is `0.0.0.0`, so a regression to a localhost default fails before any image is built.

## What this does not solve

Customers deliberately binding to a non-default interface (rare but real — Tailscale-only deployments, sidecar reverse-proxy in the same netns) still need the override. The env var stays. The default and the silent acceptance are what change.
