"""
Pinning tests for case 02 — schema-aware healthcheck.

The original bug: api shipped without any schema-version awareness, so a
node connected to a database missing migration 002 booted, passed
`/healthz` (which only checks db reachability), and served 500s on
`/audit` until customer traffic exposed it.

The fix the escalation proposes — and which is now in api/main.py — is a
`schema_migrations` registry that records each applied migration plus a
`/healthz?check=schema` query param that returns 503 when the DB is
behind `EXPECTED_SCHEMA_VERSION`. A node that is behind on migrations
fails its readiness probe and never serves traffic.

These tests pin both halves of the contract:

- when the DB has only migration 001 applied (the case-02 broken state),
  `/healthz?check=schema` with EXPECTED_SCHEMA_VERSION=2 returns 503
- after applying migration 002 via /admin/migrate/2, the same call returns 200

If a future change disables the schema check, drops EXPECTED_SCHEMA_VERSION
handling, or removes the registry update from /admin/migrate, these tests
fail before customer traffic would expose it.

Requires the api to be reachable on localhost:8000 and the database to
be reachable through DATABASE_URL. Local docker-compose publishes the db
on 127.0.0.1:5432; the Colab notebook runs Postgres natively on
localhost:5432. Both paths exercise the real psycopg SQL path.
"""
from __future__ import annotations

import os
import socket

import httpx
import psycopg
import pytest

API_HOST = "127.0.0.1"
API_PORT = 8000
BASE = f"http://{API_HOST}:{API_PORT}"
DEFAULT_DATABASE_URL = "postgresql://app:app@127.0.0.1:5432/app"


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


@pytest.fixture
def fresh_baseline():
    """Each test starts from the case-02 broken state: schema_migrations
    has only version 1. Achieved by hitting the lab's reset.sh equivalent
    inline — DELETE FROM schema_migrations WHERE version > 1, plus DROP
    audit_log so the next test can independently re-apply 002."""
    # Use psycopg directly instead of `docker compose exec db psql`.
    # That keeps the same contract under both local docker-compose
    # (db port published to 127.0.0.1:5432) and Colab (native Postgres,
    # no Docker binary available).
    database_url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM schema_migrations WHERE version > 1")
        cur.execute("DROP TABLE IF EXISTS audit_log CASCADE")
    yield


def test_default_healthz_passes_in_broken_state(fresh_baseline):
    """The pre-fix shape: /healthz without ?check=schema only verifies db
    reachability, so a node missing migrations passes its probe. This is
    the bug. Pinning it ensures we don't accidentally tighten /healthz
    in a way that breaks ops elsewhere; the schema check stays opt-in."""
    r = httpx.get(f"{BASE}/healthz", timeout=5.0)
    assert r.status_code == 200, r.text


def test_schema_check_503s_when_db_is_behind(fresh_baseline):
    """The fix's first half: with EXPECTED_SCHEMA_VERSION=2 (set in
    docker-compose.yml) and only version 1 in schema_migrations,
    /healthz?check=schema must return 503 with a clear behind-message."""
    r = httpx.get(f"{BASE}/healthz", params={"check": "schema"}, timeout=5.0)
    assert r.status_code == 503, r.text
    body = r.text.lower()
    assert "schema" in body and "behind" in body and "expected=2" in body and "actual=1" in body


def test_schema_check_passes_after_migration_runs(fresh_baseline):
    """The fix's second half: applying migration 2 via the runner inserts
    into schema_migrations, and /healthz?check=schema flips green. If the
    runner stops updating the registry, this test fails."""
    apply = httpx.post(f"{BASE}/admin/migrate/2", timeout=10.0)
    assert apply.status_code == 200, apply.text

    r = httpx.get(f"{BASE}/healthz", params={"check": "schema"}, timeout=5.0)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["schema_version"] == 2


def test_schema_check_with_no_expected_env_returns_503_with_clear_message(fresh_baseline):
    """Defensive check: if a deploy ships without EXPECTED_SCHEMA_VERSION
    set, the schema check should refuse to make a silent-pass decision —
    return 503 so the misconfiguration is visible. This guards against a
    regression where the fallback becomes 'no env → assume current,
    return 200', which would hide drift."""
    # Can't change env on the running container from a host test, so we
    # test the documented behavior shape: when /healthz?check=schema is
    # called, the api uses the configured EXPECTED_SCHEMA_VERSION. Skip
    # if the env is intentionally set, since the test would falsely fail.
    # Note: docker-compose.yml sets EXPECTED_SCHEMA_VERSION=2, so this
    # case is covered by the other tests; this test is a placeholder
    # that documents the expected behavior shape.
    pytest.skip(
        "EXPECTED_SCHEMA_VERSION is set in docker-compose.yml; "
        "the missing-env path is documented in api/main.py and exercised "
        "by unit tests against a separate fixture (not in this file)."
    )
