"""
Pinning tests for case 05 — N+1 fix on /orders.

The original `/orders` endpoint issues one query per order to look up the
customer name, totaling `limit + 1` queries per request. The fix lives
alongside it at `/orders/v2`: one query for the order page, one query
for the unique customer ids in that page, joined in Python. Query count
is bounded at 2 regardless of `limit`.

The pin: hit `/orders/v2?limit=200`, parse the diag block, assert
`queries <= 2`. If a future refactor of /orders/v2 reverts to the per-row
pattern (or anyone re-introduces a `cur.execute` inside the loop), the
query count blows past 2 and this test fails before traffic shape would
expose it again.

Requires the docker-compose stack to be up — the test exercises the live
HTTP path including the real psycopg connection, since query count is
exactly the contract that mocks would hide.
"""
from __future__ import annotations

import socket

import httpx
import pytest

API_HOST = "127.0.0.1"
API_PORT = 8000


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


def _orders_v2(limit: int) -> dict:
    r = httpx.get(f"http://{API_HOST}:{API_PORT}/orders/v2", params={"limit": limit}, timeout=10.0)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("limit", [1, 10, 50, 200])
def test_orders_v2_query_count_is_bounded_at_two(limit: int):
    """The contract: regardless of how many rows the page returns, the
    endpoint runs at most two queries against the database. This is the
    fix's invariant; an N+1 regression would push the count to limit+1."""
    body = _orders_v2(limit)
    assert body["diag"]["queries"] <= 2, (
        f"/orders/v2?limit={limit} ran {body['diag']['queries']} queries; "
        f"the fix's contract is <= 2 regardless of limit. An N+1 regression "
        f"is the most likely cause."
    )


def test_orders_v2_returns_customer_names():
    """Sanity: the rewrite did not lose customer-name resolution. If the
    customers WHERE id = ANY(...) query is broken or its result mapping
    drops names, this catches it."""
    body = _orders_v2(limit=10)
    assert len(body["orders"]) > 0
    # Every row should have a non-null customer name (the seed populates
    # customer_id from a real customers.id; nothing in the page should
    # have an unresolved customer).
    rows_without_customer = [r for r in body["orders"] if r.get("customer") is None]
    assert rows_without_customer == [], (
        f"/orders/v2 returned rows with null customer field: {rows_without_customer}. "
        f"The customer-name resolution is broken."
    )


def test_orders_v2_query_count_is_at_least_one_when_orders_exist():
    """Negative invariant: query count must be >= 1 when there are rows
    to fetch. A regression that mocks or short-circuits the db call (and
    happens to return seemingly-correct data) would set queries=0; the
    bounded test alone wouldn't catch that — this one does."""
    body = _orders_v2(limit=50)
    if body["orders"]:
        assert body["diag"]["queries"] >= 1
