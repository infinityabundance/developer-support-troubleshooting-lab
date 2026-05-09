# Developer Support Troubleshooting Lab

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/infinityabundance/developer-support-troubleshooting-lab/blob/main/colab/run_lab.ipynb)

**Try it now (no install required)**

Click the **Open in Colab** badge above.

1. Open the Colab notebook
2. Click ► Run all

---

This is the lab I would build for every new support hire. Seven cases across seven diagnostic registers (auth, database, container networking, webhook integration, performance, Linux/DNS, TLS), each a realistic failure mode that ships in production support queues. Every case carries a reproduction that runs end-to-end in CI, the captured logs from a real broken run, the customer-facing response, the engineering escalation, the fix, and a pinning test that fails when the fix is reverted. Treat it as training for the diagnostic muscle, not as a portfolio of incidents I personally responded to in production.

A representative case, in the voice the rest of the cases are written in:

> Customer ticket: every webhook event from a previously-working sender is rejected with `bad signature`.
>
> Three hypotheses, two killed cheaply: clock skew (replayed with the sender's exact timestamp — still mismatched), wrong secret (computed the HMAC by hand — bytes off). The `body_len=` field on the receiver's log line had a different value than the sender claimed it had sent. Receiver was running `await request.json()` before computing the HMAC, then signing the re-serialized form. Different whitespace, different bytes, different signature.
>
> Fix: capture `await request.body()` first, verify HMAC over the raw bytes, then parse. Pinning test in CI fails before the fix and passes after.

That is case 04 of seven. The other six diagnostic registers are auth (JWT audience), database (partial migration), container networking (loopback bind), performance (N+1 query), Linux/DNS (Alpine + musl + ndots), and TLS (incomplete certificate chain). The case writeups follow the same five-section postmortem template so the practice of *reading* one carries over to writing the next.

## Cases

| # | Case | Layer |
|---|------|-------|
| 01 | [401 from JWT audience-claim mismatch](cases/01-jwt-audience-mismatch/) | Auth |
| 02 | [Postgres "relation does not exist" after partial migration](cases/02-postgres-missing-relation/) | Database |
| 03 | [Container reachable from inside, refused from host (bind 127.0.0.1)](cases/03-container-bind-127001/) | Network |
| 04 | [Webhook HMAC fails after JSON re-serialization](cases/04-webhook-hmac-reserialization/) | Integration |
| 05 | [Slow `/orders` endpoint due to N+1 query](cases/05-endpoint-n-plus-one/) | Performance |
| 06 | [Intermittent DNS in Alpine container (`ndots`/musl)](cases/06-dns-ndots-musl/) | Linux/DNS |
| 07 | [TLS handshake fails: server presents leaf only, intermediate missing](cases/07-tls-incomplete-chain/) | TLS |

See also [`TRIAGE.md`](TRIAGE.md) for the first-30-seconds checklist used on any incoming ticket, and [`docs/support-casebook.md`](docs/support-casebook.md) for all cases in one document.

## Quickstart

Requirements: Docker, Docker Compose, GNU Make.

```bash
make up                            # build and start the platform in known-good state
make reproduce-01                  # reproduce case 01 end-to-end (idempotent)
make reproduce-all                 # reproduce every case in order, with reset between
make trace REQUEST=<request-id>    # cross-service log trace for a single request
make down                          # tear everything down
```

Every `reproduce.sh` is idempotent and self-resets the platform before running. `make trace` greps the api and db log streams for a single request id, then prints the matched lines time-ordered with each prefixed by service name — see `cases/04-webhook-hmac-reserialization/README.md` for a worked example.

To run the pinning tests under `tests/` (each one fails if the corresponding fix is reverted), install the test deps once and invoke pytest:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r tests/requirements.txt
make up         # several pinning tests need the live stack
pytest -q
```

### Windows 11 VM

See [docs/windows-11-vm.md](docs/windows-11-vm.md) for the Windows 11 VM setup.
Short version: Docker Desktop runs on Windows, Ubuntu on WSL2 is only the Linux
shell for repo tooling, and Docker must be reached through Docker Desktop's WSL
integration. Do not install Docker Engine inside Ubuntu for this path.

CI runs the full suite (reproductions + pinning tests) on Python 3.11 / 3.12 / 3.13 with one automatic retry for timing-sensitive cases, plus a nightly schedule trigger to catch upstream image drift.

## Repository layout

```
api/                  FastAPI service (auth, audit, webhook, orders, healthz,
                      admin/migrate); broken endpoints alongside their /v2 fixes
bin/                  trace.sh — cross-service log tracer used by `make trace`
cases/NN-slug/        per-case folder: README.md, reproduce.sh, logs.txt
                      (captured), customer-response.md, engineering-escalation.md,
                      expected-output.txt; case 07 also carries tls_server.py
db/migrations/        001_init.sql (auto-applied; seeds schema_migrations v1),
                      002_partial.sql (case 02's "missing migration")
docs/                 support-casebook.md (single-document casebook),
                      support-casebook.typ (typst source), support-casebook.pdf,
                      windows-11-vm.md (Windows VM setup)
seed/                 reset.sh — idempotent baseline reset between cases
tests/                test_reproductions.py (runs every reproduce.sh and diffs
                      stdout against expected-output.txt) plus per-case pinning
                      tests (test_auth, test_bind, test_orders, test_cert_chain,
                      test_schema_check, test_trace, test_webhook); requirements.txt
                      installs the test-only deps (fastapi, pyjwt, httpx, pytest)
.github/workflows/    CI: lint, build platform, run reproductions + pinning tests
                      across Python 3.11/3.12/3.13 with one auto-retry; nightly
                      schedule trigger to catch upstream image drift
TRIAGE.md             first-30-seconds checklist used on any incoming ticket;
                      steps 1–7 mechanical, steps 8–11 the political/judgment layer
docker-compose.yml    db + api + alpine-resolver (case 06 sidecar);
                      EXPECTED_SCHEMA_VERSION baked into the api image
Makefile              up / down / reset / reproduce-NN / reproduce-all / trace /
                      test / lint / clean
```

## What this repo demonstrates

Practical fitness for: Developer Support Engineer, Platform Support Engineer,
Linux Support Engineer, API Support Engineer, Integration Support Engineer,
Technical Escalation Engineer.

What it deliberately does not demonstrate: frontend engineering, ML, or
greenfield product engineering. Different repo for those.


## License

Apache 2.0 (reference implementation). Background IP: Invariant Forge LLC.
Commercial deployment requires separate written license.
Contact: licensing@invariantforge.net
