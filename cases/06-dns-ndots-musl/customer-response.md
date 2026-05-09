Hi,

The "intermittent" framing is reasonable but a little misleading — what's actually happening is that the resolution is deterministic per state of your resolv.conf and your worker's restart timing, and it looks random from outside because the state can vary between restarts.

Two effects compound on Alpine:

1. Alpine ships with the musl libc resolver, which handles `search` and `ndots` differently than glibc. Lookups that work on a Debian/Ubuntu base can miss on Alpine for the same hostname.
2. Docker's embedded DNS at `127.0.0.11` resolves short service names on custom networks, but the musl resolver's interaction with the search list and ndots can rewrite the lookup in a way that misses the embedded DNS path.

Two ways to make this reliable today:

**Quick fix:** in your worker, refer to the api as `api.` (note the trailing dot — fully qualified). That bypasses the search-list rewriting entirely. One-character change in your config.

**Durable fix:** switch the worker base image from Alpine to a glibc-based image (e.g. `python:3.12-slim`). Alpine is a real source of DNS surprise for any workload doing runtime resolution, and the image-size savings rarely cover the operational cost.

I'd recommend the durable fix. The quick fix is fine if rebuilding the image is expensive this week.

Let me know which path you want and I can help validate the resolution behavior on your end.
