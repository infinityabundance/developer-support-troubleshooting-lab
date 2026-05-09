"""
Pinning tests for case 03 — container bind reachability.

The original bug: api ran with --host 127.0.0.1 inside the container,
which is the container's loopback, not the host's. The in-container
healthcheck passed (process can talk to itself); the host curl on the
published port failed (request from outside arrives on the container's
external interface, where uvicorn isn't listening).

These tests pin the two contracts that actually matter:

1. The static config defaults to 0.0.0.0 (test_default_bind_host_is_zero_in_compose).
   Catches a regression at the docker-compose.yml level — someone setting
   the default back to 127.0.0.1 — before any image rebuild.

2. The published port is reachable from outside the container
   (test_default_bind_is_reachable_from_host). Catches anything the static
   check misses: broken port publish, image CMD overriding the env, host
   firewall, port-forwarder mis-routing.

Test 2 requires the docker-compose stack to be up via `make up`. Test 1
runs without the stack.
"""
from __future__ import annotations

import re
import socket
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
API_HOST = "127.0.0.1"
API_PORT = 8000


def _stack_is_up(host: str = API_HOST, port: int = API_PORT, timeout: float = 0.5) -> bool:
    """Quick TCP probe to decide whether to skip integration-shaped tests."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def test_default_bind_host_is_zero_in_compose():
    """Static check: docker-compose.yml's api service defaults BIND_HOST to
    0.0.0.0 (or accepts an override but falls back to 0.0.0.0). A regression
    that flips the default back to 127.0.0.1 — which is what shipped the
    original bug — fails this test before any container is built."""
    text = COMPOSE_FILE.read_text()
    # Look for `BIND_HOST: ${BIND_HOST:-0.0.0.0}` or any equivalent default.
    # The default value must be 0.0.0.0; a default of 127.0.0.1, localhost,
    # or [::1] would be the regressed shape.
    pattern = re.compile(r"BIND_HOST\s*:\s*\$\{BIND_HOST:-([^}]+)\}")
    match = pattern.search(text)
    assert match is not None, (
        f"BIND_HOST env var with default not found in {COMPOSE_FILE}. "
        f"This test asserts the api service has an explicit default; "
        f"the case 03 fix relies on it."
    )
    default = match.group(1).strip()
    assert default == "0.0.0.0", (
        f"BIND_HOST default in docker-compose.yml is {default!r}; case 03's "
        f"fix requires 0.0.0.0. A regressed value here ships the original bug."
    )


@pytest.mark.skipif(not _stack_is_up(), reason="docker-compose stack not up; run `make up` first")
def test_host_can_reach_default_bind_via_published_port():
    """The from-outside path that the in-container healthcheck cannot
    test. If the api binds 127.0.0.1 inside the container, this fails
    even though the container's own healthcheck passes — the published
    port is the bridge that has to actually deliver the request to the
    process, and that's the path being pinned here."""
    r = httpx.get(f"http://{API_HOST}:{API_PORT}/healthz", timeout=5.0)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}


@pytest.mark.skipif(not _stack_is_up(), reason="docker-compose stack not up; run `make up` first")
def test_request_id_is_propagated_round_trip():
    """A second from-outside path. The middleware echoes the request id
    in the response header; if the request never made it past the
    container's network namespace, no header comes back. This pins the
    full request/response flow that the bind bug breaks."""
    rid = "pinning-test-bind-rid-x"
    r = httpx.get(
        f"http://{API_HOST}:{API_PORT}/healthz",
        headers={"x-request-id": rid},
        timeout=5.0,
    )
    assert r.status_code == 200
    assert r.headers.get("x-request-id") == rid
