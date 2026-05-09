# Case 05 — `/orders` p99 spikes from 50ms to 1.4s under load (N+1)

## Symptom (as reported)

> Customer ticket #4602, P3 escalated to P2.
> "Your `/orders` endpoint is slow. Single requests are fine (~50ms), but our reporting batch hits it 200 times in a row and we see p99 around 1.4 seconds. Nothing in your status page mentions an issue. We pulled this off our APM."

## Reproduction

```bash
./reproduce.sh
```

Hits `/orders?limit=200` against the broken implementation, prints the per-request `queries=` count and `dur_ms=` from the API logs.

## Diagnostic narrative

The API logs the query count and the request duration on every `/orders` call. That single log field — `queries=N+1` for N orders — is the entire diagnosis.

```
endpoint=/orders rows=200 queries=201 dur_ms=380.4
```

Reading 200 orders cost 201 queries: one to fetch the orders, then one per order to fetch the customer name. Classic N+1.

The proper test, before believing the log line: `EXPLAIN ANALYZE` the per-customer query in `psql`. It plans as `Index Scan using customers_pkey` if the PK index is in place — fast per-call, but called 200 times. The cost isn't in the plan, it's in the round-trip count.

`pg_stat_statements` confirms the same shape:

```
 query                                     | calls | total_exec_time
-------------------------------------------+-------+-----------------
 SELECT name FROM customers WHERE id = $1  |  20100 |  4283.7
 SELECT id, customer_id, ...               |    100 |    21.2
```

20100 calls of the per-customer query versus 100 of the bulk-orders query. Same shape on every batch. That ratio tells the story even without reading the code.

## Evidence

- Log line: `endpoint=/orders rows=200 queries=201`
- `pg_stat_statements` snapshot in `logs.txt`
- `EXPLAIN ANALYZE` of both queries in `logs.txt`

## Root cause

`api/main.py::orders` issues a separate `SELECT name FROM customers WHERE id = $1` for every order in the result set. With 200 orders and 10 distinct customers, the endpoint issues 201 queries instead of the 1 query a JOIN or 2 queries a batched lookup would use.

Why this slipped through: locally, with a small dev dataset, the duration is fine. The endpoint is well within p50 SLO. The problem only shows up under realistic batch sizes.

## Fix

**Workaround:** the customer can rate-limit their reporting batch or page in smaller chunks (`?limit=50`). This reduces the spike but does not fix the underlying scaling.

**Proper fix:** rewrite the endpoint to use a single query with a JOIN, or two queries with an `IN (...)` for the customer lookup. The two-query version is more readable and indexes cleanly:

```python
cur.execute("SELECT id, customer_id, amount_cents FROM orders ORDER BY id LIMIT %s", (limit,))
rows = cur.fetchall()
ids = list({cid for _, cid, _ in rows})
cur.execute("SELECT id, name FROM customers WHERE id = ANY(%s)", (ids,))
names = dict(cur.fetchall())
out = [{"id": oid, "customer": names.get(cid), "amount_cents": amt} for oid, cid, amt in rows]
```

The fix lives at `/orders/v2` in `api/main.py`, alongside the still-broken `/orders` so the case's reproduction script keeps demonstrating the N+1. Hitting `/orders/v2?limit=200` returns `queries=2` regardless of order count.

Companion changes:
- `idx_orders_customer_id` (in 002_partial.sql) — not strictly required for the IN-lookup but speeds up customer-side joins elsewhere; ships with migration 002.
- The regression tests in `tests/test_orders.py` (`test_orders_v2_query_count_is_bounded_at_two` parametrized over `limit ∈ {1, 10, 50, 200}`, plus `test_orders_v2_returns_customer_names`) pin the query-count contract.

## Outcome

Latency under the fix would be whatever the per-query round-trip is, times two. The fix's hard contract is the *query count*: 2 instead of `limit + 1`. The wall-clock improvement that flows from that depends on per-query round-trip and connection-pool state in the customer's environment, neither of which we can claim from this lab's hardware. As an order-of-magnitude sketch: on a host where each round-trip costs ~7 ms, the broken path would be ~7 ms × 201 ≈ 1.4 s and the fixed path would be ~7 ms × 2 ≈ 14 ms; on a slower path with ~30 ms per round-trip, the broken path would be ~6 s and the fixed path ~60 ms. The regression test (`tests/test_orders.py::test_orders_v2_query_count_is_bounded_at_two`) pins the query count, not the timing, because query count is the actual failure mode and is invariant across hardware.

## Adjacent failure modes (not hit in this case, but the same pattern)

- **Lazy ORM relationships triggered inside a serializer.** SQLAlchemy `lazy='select'` (or the equivalent in Django ORM, ActiveRecord, etc.) fires a SELECT each time a relationship attribute is accessed. A list comprehension over a 200-row queryset that touches `order.customer.name` issues 200 round-trips even though the explicit Python looks single-query. Diagnostic: log `queries=` per request — the number doesn't match the number of `cur.execute(...)` calls in your handler, because the ORM is the one issuing them.
- **Connection-pool starvation, not query count.** The `queries=` metric isn't the only failure shape. With a pool size of 5 and a 1-query-per-row endpoint hit by 50 concurrent requests, the bottleneck is pool-acquire wait, not query count per request. p99 spikes; `queries=` per request looks normal because each individual request still does its N+1 sequentially against one pooled connection. Diagnostic: log pool-acquire wait time alongside query count.
- **Implicit query in a hot loop with constant arguments.** Same query repeated inside a loop with arguments that don't actually vary across iterations — the query result is the same every time, but the round-trip happens each iteration. Caches built into the ORM layer often help here; raw SQL handlers don't, and a code reviewer can miss it because the loop body looks parameterized. Fix is to hoist the query out of the loop, not to rewrite as ANY().
