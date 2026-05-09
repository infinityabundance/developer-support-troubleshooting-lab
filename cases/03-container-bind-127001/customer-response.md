Hi,

Your container is healthy. What's failing is the path from the host into it.

The fix is one env var: set `BIND_HOST=0.0.0.0` on the api service in your Compose file (or pass `--host 0.0.0.0` to your start command). Restart the container. `curl http://localhost:8000/healthz` from the host will work immediately after.

The reason the in-container healthcheck passed but the host curl didn't: those two checks talk to different network interfaces. The in-container check uses the container's loopback; the host curl arrives on the container's external interface. They're separate paths, and only one of them was being served.

While you're in the config: it's worth a quick grep across the rest of your stack for `--host 127.0.0.1` or `--host localhost`. This is the pattern that gets copied out of local-dev Procfiles or shell scripts into container images, and it doesn't fail until something tries to reach the service from outside the container — which often isn't until prod.

Reply once you've restarted with the change and I'll close the ticket.
