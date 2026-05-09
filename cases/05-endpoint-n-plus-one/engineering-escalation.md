# Escalation: `/orders` N+1 — per-row customer lookup

**Severity:** P2 (degraded SLO under realistic batch sizes; multiple customers affected)
**Component:** `api/main.py::orders`
**Triggering ticket:** #4602

## What happened

`/orders` issues `SELECT name FROM customers WHERE id = $1` once per order in the response. With 200 orders, that's 201 queries. Per-query latency is fine; the cost is in the round-trip count and the connection pool contention under concurrent batches.

## Why this got past review

Local development data has 10 orders. The endpoint took <5ms locally. The dataset used in CI was the same 10-row set. There was no load test, and no assertion on query count. The bug was statistically invisible until production traffic shape exposed it.

## Proposed fix

Two-query pattern with `IN`/`ANY`:

```python
cur.execute("SELECT id, customer_id, amount_cents FROM orders ORDER BY id LIMIT %s", (limit,))
rows = cur.fetchall()
ids = list({cid for _, cid, _ in rows})
cur.execute("SELECT id, name FROM customers WHERE id = ANY(%s)", (ids,))
names = dict(cur.fetchall())
out = [{"id": oid, "customer": names.get(cid), "amount_cents": amt} for oid, cid, amt in rows]
```

Why two queries instead of a JOIN: the customer list is small and reused; a JOIN would re-emit `name` per order row, increasing payload size. Two queries keeps the row shape clean and lets the customer lookup hit the customers PK index once.

## Test changes

`tests/test_orders.py` — the fix lives at `/orders/v2` alongside the broken `/orders` so the case still demonstrates the bug:

1. `test_orders_v2_query_count_is_bounded_at_two` — parametrized over `limit ∈ {1, 10, 50, 200}`, hits `/orders/v2`, parses the diag block, asserts `queries <= 2`. The contract is exact; the duration assertion is left out because it's hardware-dependent and would flake in CI.
2. `test_orders_v2_returns_customer_names` — sanity check that the two-query rewrite didn't drop the join-side `customer` field.
3. `test_orders_v2_query_count_is_at_least_one_when_orders_exist` — guard against a regression that mocks or short-circuits the db call (which would set `queries=0` and silently pass the upper-bound check alone).

## What this does not solve

- Concurrent batches still contend on the connection pool. If multiple customers hit `/orders?limit=500` simultaneously, pool size becomes the bottleneck. Separate ticket on pool sizing.
- The endpoint has no pagination on the orders side beyond `limit`. A real fix for very large batches is keyset pagination. Not blocking this ticket.
