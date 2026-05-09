Hi,

Confirmed and reproduced. `/orders` is doing one extra database lookup per row in the response — fast on a single request, slow on a 200-row batch. That's why your single-request timing was fine and your batch p99 was 1.4s. Your APM was reading exactly what we're seeing.

A rewrite of the endpoint to fetch customers in a single follow-up query (instead of one per order) is going out today. Once it lands, the same 200-row batch should drop from ~1.4s to under 30ms. We'll also add a regression test that fails if the per-row query pattern is reintroduced.

If you need an immediate workaround before the rollout, paging your batch with `?limit=50` per request will keep individual response times under 200ms. Not a fix, but it should keep your reporting job from hitting the spike.

I'll update this ticket when the fix is live and again once the test is in CI.
