# Case 03 — Container reachable from inside, refused from host (bound to 127.0.0.1)

## Symptom (as reported)

> Customer ticket #4533, P2.
> "We deployed your reference Compose stack on a fresh VM. The api container is up and the healthcheck inside the container passes, but `curl http://localhost:8000/healthz` from the host returns `Connection refused`. Port 8000 is published in the compose file. What are we missing?"

## Reproduction

```bash
./reproduce.sh
```

The script restarts the api container with `BIND_HOST=127.0.0.1`, then issues `curl http://localhost:8000/healthz` from the host. The host curl fails with connection refused. A curl from *inside* the container succeeds.

## Diagnostic narrative

The service appears to be running — `docker compose ps` shows it healthy, and the in-container healthcheck (`wget -qO- http://localhost:8000/healthz`) returns 200. So the application is alive. What is failing is the host-to-container path on the published port.

First test, easiest to run, ruled the most out: `docker compose ps` and read the published port column. Compose is publishing `0.0.0.0:8000->8000/tcp`. So Docker thinks the port is forwarded.

Second test: from the host, `ss -tulpn | grep 8000`. Shows nothing listening on the host on 8000. That contradicts the publish — it shouldn't, because Docker's userland proxy or iptables rule should be visible. Closer look: docker-proxy is listening on `0.0.0.0:8000` (it's a separate process, sometimes filtered by `ss` output depending on caps). The forwarding is in place.

Third test: from inside the container, `ss -tulpn | grep 8000`. Shows uvicorn listening on `127.0.0.1:8000`. Not `0.0.0.0:8000`. That is the bug.

`127.0.0.1` inside the container is the container's loopback interface, not the host's. When Docker forwards traffic from the host's `0.0.0.0:8000` into the container, it arrives on the container's `eth0` interface, not loopback. uvicorn isn't listening there, so the connection is refused at the application layer — exactly the kernel reset the host-side curl reports.

## Evidence

From the host:
```
$ curl -v http://localhost:8000/healthz
*   Trying 127.0.0.1:8000...
* connect to 127.0.0.1 port 8000 failed: Connection refused
```

From the host, `ss` on the container's network namespace (via `docker compose exec api ss -tulpn`):
```
Netid State  Recv-Q Send-Q Local Address:Port  Peer Address:Port
tcp   LISTEN 0      2048   127.0.0.1:8000      0.0.0.0:*
```

That `127.0.0.1:8000` is the smoking gun. It should be `0.0.0.0:8000` or `*:8000`.

## Root cause

uvicorn was started with `--host 127.0.0.1` (in this lab, via `BIND_HOST=127.0.0.1`). The customer's analogue is almost always one of: a default in a Procfile, a hardcoded `127.0.0.1` in a `CMD` line, or a config file that defaults to `localhost`. The container's loopback is not the host's loopback. From outside the container, that bind is unreachable.

## Fix

**Workaround (immediate):** set `BIND_HOST=0.0.0.0` on the api service and restart it.

**Proper fix:** the application's Dockerfile or entrypoint should default the bind host to `0.0.0.0` whenever it is running in a container. There is no good reason to bind a containerized service to its loopback. The default in this repo's Dockerfile is now `0.0.0.0`, with the env var as an override for cases where a sidecar reverse-proxy is in use.

## Outcome

Host curl returns 200 after the bind change. The case ships with the broken bind reproducible behind a single env var so a new engineer onboarding can see the failure mode end-to-end.

## Adjacent failure modes (not hit in this case, but the same pattern)

- **IPv6-only bind on an IPv4-only host.** Service binds `[::1]:8000`; client connects to `127.0.0.1:8000`. The two loopbacks are *separate* sockets, not aliases — `::1` doesn't accept v4 traffic. Symptom is identical to this case (port published, app healthy, host curl refused). Diagnostic: `ss -tulpn` shows `[::1]:8000` only, no v4 listener. Fix: bind `::` (dual-stack) or `0.0.0.0` (v4) explicitly.
- **Hostname-based bind on a multi-NIC VM.** Service starts with `--host my.internal.host`; the hostname resolves to one NIC's address; traffic arrives on a different NIC. The listener is real and the route is real; they just don't intersect. Symptom: works from one peer, fails from another, and the success/fail pattern correlates with which NIC the peer's route lands on. Diagnostic: `ss -tulpn` + `ip route show table all` + correlate.
- **macOS Docker Desktop's `127.0.0.1` is not the Mac.** Inside a Docker Desktop container, `127.0.0.1` is the container itself; the Mac host is `host.docker.internal`. Code that calls a "local" service via `127.0.0.1:5432` from inside the container is talking to itself, not to the dev's Postgres on the Mac. Same root cause class: assuming loopback is the same loopback you mean. Fix is `host.docker.internal`, or run the dependency in a sidecar container.

## Runtime portability note

The host-side curl exit code differs by container runtime. Docker's userland proxy answers the SYN and then has nothing to relay to, so curl typically reports `exit 7` (connection refused). Podman's `rootlessport` accepts the SYN at the host level and resets when the relayed connection fails, so curl reports `exit 56` (recv failure). On macOS Docker Desktop the same scenario can surface as `exit 28` (connect timeout) when the VM's port-forwarder times out. The deterministic signal across all three is `http_code=000` — the request never produced an HTTP response. The contract test asserts `http_code=000` plus a regex over the curl exit code, not a single hardcoded value, because the failure is "host can't reach the service" regardless of which low-level errno surfaces.
