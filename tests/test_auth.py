"""
Pinning tests for case 01 — JWT audience verification.

The original bug shipped because the verifier's tests minted tokens with
the same env var the verifier read for its accept-list. Test and code
shared a source of truth, so the test could not see the gap.

These tests deliberately break that symmetry. The audience values minted
below are hardcoded constants chosen to NOT match any real environment's
JWT_AUDIENCES setting. The verifier's accept-list is set explicitly here
to a list that includes some of these constants and excludes others. A
refactor that silently disables audience validation, or that reverts to
single-string-only handling, will fail these tests immediately because
the test mint values are not derived from the verifier's config.

Runs against the in-process FastAPI app via TestClient. Does not require
the docker-compose stack to be up.
"""
from __future__ import annotations

import sys
from pathlib import Path

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
API_DIR = REPO_ROOT / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))


# Audience values the test uses. These are not derived from any env var:
# changing JWT_AUDIENCES on the verifier does not change these.
AUD_IN_LIST_A = "audience-pinning-alpha"
AUD_IN_LIST_B = "audience-pinning-beta"
AUD_NOT_IN_LIST = "audience-pinning-gamma-not-in-list"
TEST_SECRET = "test-secret-not-shared-with-prod"


@pytest.fixture
def client(monkeypatch):
    # Verifier's accept-list explicitly contains A and B, excludes gamma.
    # The list is multi-valued to exercise the post-fix code path
    # (the original bug was a single-string accept value).
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("JWT_AUDIENCES", f"{AUD_IN_LIST_A},{AUD_IN_LIST_B}")
    # Legacy env still required by the api module's fallback logic; value
    # irrelevant when JWT_AUDIENCES is set.
    monkeypatch.setenv("JWT_AUDIENCE", "irrelevant-fallback")
    monkeypatch.setenv("WEBHOOK_SECRET", "irrelevant-for-this-test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:app@127.0.0.1:5432/app")

    # Reload main so it picks up the patched env.
    if "main" in sys.modules:
        del sys.modules["main"]
    import main  # noqa: WPS433  -- intentional in-fixture import after env patch

    return TestClient(main.app)


def _mint(aud: str, secret: str = TEST_SECRET) -> str:
    return pyjwt.encode({"sub": "u1", "aud": aud}, secret, algorithm="HS256")


def test_audience_in_configured_list_is_accepted(client):
    """First member of the accept-list passes. If this fails, the verifier
    has stopped honoring multi-valued JWT_AUDIENCES."""
    token = _mint(AUD_IN_LIST_A)
    r = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert r.json()["aud"] == AUD_IN_LIST_A


def test_other_audience_in_configured_list_is_accepted(client):
    """Second member of the same list also passes — the list isn't being
    treated as a single string."""
    token = _mint(AUD_IN_LIST_B)
    r = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert r.json()["aud"] == AUD_IN_LIST_B


def test_audience_not_in_configured_list_is_rejected(client):
    """The symmetry break: the test mints with a value NOT in any config
    the verifier knows. If the verifier ever stops checking audience,
    this test fails."""
    token = _mint(AUD_NOT_IN_LIST)
    r = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401, r.text
    assert "invalid audience" in r.text.lower()


def test_signature_with_wrong_secret_is_rejected(client):
    """Signature check happens; the audience check is not the only gate.
    Mints with a secret the verifier does not hold."""
    token = _mint(AUD_IN_LIST_A, secret="some-other-secret")
    r = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401, r.text


def test_no_authorization_header_is_rejected(client):
    r = client.get("/me")
    assert r.status_code == 401, r.text


def test_legacy_jwt_audience_env_is_honored_when_jwt_audiences_is_unset(monkeypatch):
    """Backward-compatibility check: case 01's reproduce.sh uses the
    single-string JWT_AUDIENCE env var to demonstrate the pre-fix shape.
    The api code must still accept that fallback so the case keeps
    reproducing the original bug."""
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    monkeypatch.delenv("JWT_AUDIENCES", raising=False)
    monkeypatch.setenv("JWT_AUDIENCE", "legacy-only-aud")
    monkeypatch.setenv("WEBHOOK_SECRET", "irrelevant")
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:app@127.0.0.1:5432/app")

    if "main" in sys.modules:
        del sys.modules["main"]
    import main  # noqa: WPS433

    legacy_client = TestClient(main.app)

    accepted = _mint("legacy-only-aud")
    rejected = _mint("anything-else")

    r1 = legacy_client.get("/me", headers={"Authorization": f"Bearer {accepted}"})
    r2 = legacy_client.get("/me", headers={"Authorization": f"Bearer {rejected}"})

    assert r1.status_code == 200, r1.text
    assert r2.status_code == 401, r2.text
