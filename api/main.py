"""
api/main.py — the deliberately-broken FastAPI service that the seven cases
under cases/ reproduce against.

Architectural shape
-------------------
Every case in this lab has a paired set of artefacts in the cases/ tree
(reproduce.sh, README, customer-response, engineering-escalation, captured
logs, expected-output). The corresponding *code* lives here. Cases that
have a fix shipped in this codebase keep the broken endpoint alive at its
original path and add the fixed version at `<path>/v2` (see /webhook +
/webhook/v2 for case 04, /orders + /orders/v2 for case 05). The reason
for the fix-alongside-broken pattern: the case's reproduce.sh script
expects to demonstrate the bug shape end-to-end on every CI run. Removing
the broken endpoint to "ship the fix" would silently break the case's
reproduction. The cost of carrying both is one extra route handler per
case; the benefit is that the bug remains observable forever.

Endpoints, grouped by case:

  /healthz              [also /healthz?check=schema]   (default + case 02 fix)
  /me                                                  (case 01)
  /audit                                               (case 02 broken state)
  /webhook              + /webhook/v2                  (case 04 broken + fix)
  /orders               + /orders/v2                   (case 05 broken + fix)
  /admin/migrate/{step}                                (operational helper)

Cases 03 (container loopback bind), 06 (Alpine + musl + ndots), and 07
(TLS chain) do not modify this file — they exercise platform-level
concerns that live in docker-compose.yml, the Dockerfile, or
cases/07-tls-incomplete-chain/tls_server.py respectively.

Cross-cutting concerns
----------------------
Every request is tagged with a request id by the
`request_id_and_timing` middleware below. The id is either taken from
the `x-request-id` header the caller sent or freshly generated. It
appears as `rid=<hex12>` in every log line emitted from a handler, and
is echoed back in the response's `x-request-id` header. The `make trace`
command (bin/trace.sh) greps the api log stream for an rid and assembles
a time-ordered cross-service view; case 04's README walks through it.

Configuration is read from environment variables at import time. The
caller (docker-compose.yml in the lab; whatever orchestration in
production) is expected to provide all five env vars below; missing any
one of the required ones is a fail-fast at boot, by design — a misconfig
should never make it past the readiness probe.
"""
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from typing import Any

import jwt
import psycopg
from fastapi import FastAPI, Header, HTTPException, Request

log = logging.getLogger("api")

# ---------- configuration (read once at import time) ----------

# HMAC secret used by jwt.decode for case 01's token verification. Required.
JWT_SECRET = os.environ["JWT_SECRET"]

# Verifier accepts a *list* of audiences. The legacy single-string env var
# (JWT_AUDIENCE) is honored as a fallback so case 01's reproduction script
# can still demonstrate the pre-fix single-audience failure mode without
# requiring an env-var migration. Read order: JWT_AUDIENCES first, then
# JWT_AUDIENCE; at least one must be set or boot fails.
#
# Why a list: the original case-01 bug shipped because the verifier was
# pinned to a single audience string and could not accept tokens from
# tenants whose IdPs issued a different audience (api-staging, tenant-
# specific values, etc.). The list form is the proposed engineering fix
# from cases/01-jwt-audience-mismatch/engineering-escalation.md, shipped
# here. Comma-separated, whitespace tolerated, empty entries dropped.
_aud_env = os.environ.get("JWT_AUDIENCES") or os.environ["JWT_AUDIENCE"]
JWT_AUDIENCES = [a.strip() for a in _aud_env.split(",") if a.strip()]

# Shared secret the webhook signer uses to compute HMAC. Required for
# both /webhook (broken) and /webhook/v2 (fixed). The signer (real or
# in case 04's reproduce.sh) is expected to know this same value.
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]

# psycopg connection string for the postgres service. Required.
DATABASE_URL = os.environ["DATABASE_URL"]

app = FastAPI()


# ---------- middleware: request id + timing log line per request ----------

@app.middleware("http")
async def request_id_and_timing(request: Request, call_next):
    """Attach a request id to `request.state` and the response, log one
    timing line per request.

    Behavior:
      - If the caller sent an `x-request-id` header, that value is used
        verbatim. Lets a customer correlate their own logs with ours by
        passing the id from their side. No length cap, no validation —
        callers that pass garbage get garbage back in their own logs.
      - Otherwise a fresh 12-hex-char id is generated. uuid4().hex[:12]
        gives 48 bits of entropy, which is plenty for a per-request
        correlation id in this lab; production would typically use a
        full uuid4 or a real distributed-tracing span id.
      - The id is exposed to handlers via `request.state.request_id`,
        echoed in the response's `x-request-id` header, and embedded in
        the per-request timing log line as `rid=<hex>`.
      - `dur_ms` is wall-clock from middleware entry to handler return,
        not server-processing time; it includes db queries, framework
        overhead, and async scheduling. Good enough for a diagnostic
        log; not suitable as an SLO source-of-truth.

    The log line format is fixed by `bin/trace.sh`, which greps for it
    by the `rid=` token. Changing the format here without updating the
    tracer breaks the trace tool silently.
    """
    rid = request.headers.get("x-request-id", uuid.uuid4().hex[:12])
    start = time.perf_counter()
    request.state.request_id = rid
    response = await call_next(request)
    dur_ms = (time.perf_counter() - start) * 1000.0
    log.info(
        "rid=%s method=%s path=%s status=%s dur_ms=%.1f",
        rid, request.method, request.url.path, response.status_code, dur_ms,
    )
    response.headers["x-request-id"] = rid
    return response


def db():
    """Open a fresh psycopg connection per call.

    Wrapped in a function (rather than a module-level connection pool)
    because every handler that touches the db uses `with db() as conn`,
    which calls `__exit__` on the connection — closing it. A pool would
    be the production choice; a per-request connection is the simpler
    choice for the lab and avoids the connection-pool-exhaustion failure
    mode that would muddy case 05's N+1 demonstration.
    """
    return psycopg.connect(DATABASE_URL)


# ---------- /healthz: liveness + optional schema-readiness ----------

@app.get("/healthz")
def healthz(check: str | None = None):
    """Default health check (no query param): `SELECT 1` against the db
    succeeds. Used by docker-compose's healthcheck to gate api-ready.

    `?check=schema` adds a schema-version readiness check on top: read
    `MAX(version)` from `schema_migrations`, compare against the
    `EXPECTED_SCHEMA_VERSION` env var the image was built with, return
    503 if the database is behind. This is the readiness gate the case
    02 escalation argues should block traffic from a node that's missing
    migrations, instead of letting it serve 500s on the affected
    endpoint.

    Why opt-in via query param instead of always-on: docker-compose's
    container healthcheck (line `wget -qO- http://localhost:8000/healthz`)
    must succeed even when the db is at a lower migration version than
    the image expects, otherwise case 02's reproduction (which
    deliberately leaves the db at version 1 while the image expects
    version 2) could not run — the api would never go ready and
    reproduce.sh would hang. Production would wire `?check=schema` into
    the readiness probe specifically, leaving the bare /healthz for
    liveness.

    Why the bare-`/healthz` path also exits the with-block early when
    `check == "schema"`: if the schema is up-to-date, returning the
    `{ok, schema_version}` shape from inside the block makes the cursor
    visit MAX(version) only once. The unhealthy paths raise
    HTTPException, which Starlette's exception machinery surfaces with
    the right status code; the outer `except HTTPException: raise`
    deliberately rethrows so the catch-all `except Exception` below
    doesn't swallow a 503 we computed and turn it into a different 503
    with a misleading "db unavailable" message.
    """
    try:
        with db() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
            if check == "schema":
                expected_env = os.environ.get("EXPECTED_SCHEMA_VERSION")
                if expected_env is None:
                    # Defensive: if the deploy ships without
                    # EXPECTED_SCHEMA_VERSION the readiness probe should
                    # *fail*, not silently return 200. Hiding misconfig
                    # is the failure mode case 02 exists to prevent.
                    raise HTTPException(
                        503,
                        "schema check requested but EXPECTED_SCHEMA_VERSION not set",
                    )
                expected = int(expected_env)
                # COALESCE handles the empty-table edge case (version 0
                # treated as "behind any positive expectation"). In the
                # lab this never fires because 001_init.sql seeds version
                # 1, but a fresh db with no bootstrap would otherwise
                # crash on `int(None)`.
                cur.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")
                actual = cur.fetchone()[0]
                if actual < expected:
                    raise HTTPException(
                        503,
                        f"schema behind: expected={expected} actual={actual}",
                    )
                return {"ok": True, "schema_version": actual}
    except HTTPException:
        # Re-raise our own 503s unchanged so the catch-all below can't
        # mislabel them as "db unavailable".
        raise
    except Exception as e:
        # Anything unexpected (db down, network blip, syntax error in a
        # migration we just attempted, etc.) surfaces here as 503 with
        # the underlying exception text. 503 because the service is up
        # but cannot serve; the load balancer should treat it as not-ready.
        raise HTTPException(503, f"db unavailable: {e}")
    return {"ok": True}


# ---------- case 01: JWT audience verification (/me) ----------
#
# The bug case 01 demonstrates: a verifier configured for a single
# audience rejects tokens whose `aud` claim is anything else, even when
# the token is otherwise valid. The fix landed below: `audience=` is now
# a list (JWT_AUDIENCES). The case's reproduce.sh still exercises the
# bug shape because the lab's compose env carries a single-value
# JWT_AUDIENCE, so the JWT_AUDIENCES-derived list contains exactly one
# entry — same behavior as the pre-fix verifier. The fix is observable
# only when the env carries multiple values (which the pinning tests in
# tests/test_auth.py do via monkeypatch).

@app.get("/me")
def me(authorization: str | None = Header(default=None), request: Request = None):
    """Decode a bearer token, return the subject + audience claims.

    Auth header expected: `Authorization: Bearer <jwt>`. Case-insensitive
    on the scheme. Anything else is a 401 with `missing bearer token`.

    Each pyjwt failure mode is logged with a distinct `auth=<reason>`
    tag so the diagnostic for a 401 is one log line, not a stack trace.
    The `expected=<list>` field on the invalid_audience log line is the
    move that makes case 01 a 30-second diagnosis (see the case README's
    "Evidence" section).
    """
    rid = request.state.request_id if request else "?"
    if not authorization or not authorization.lower().startswith("bearer "):
        log.warning("rid=%s auth=missing_bearer", rid)
        raise HTTPException(401, "missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        # pyjwt accepts either a string or a list for `audience=`. With a
        # list, the token's `aud` claim must be in the list (or the
        # token's aud is itself a list and at least one entry overlaps).
        claims = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=["HS256"],
            audience=JWT_AUDIENCES,
        )
    except jwt.InvalidAudienceError as e:
        # The expected= field is what makes case 01 fast to diagnose.
        # Stripping it would force every JWT 401 ticket to start with
        # "what audience is this verifier expecting?".
        log.warning("rid=%s auth=invalid_audience expected=%s err=%s",
                    rid, JWT_AUDIENCES, e)
        raise HTTPException(401, "invalid audience")
    except jwt.ExpiredSignatureError:
        log.warning("rid=%s auth=expired", rid)
        raise HTTPException(401, "token expired")
    except jwt.InvalidSignatureError:
        log.warning("rid=%s auth=bad_signature", rid)
        raise HTTPException(401, "invalid signature")
    except jwt.InvalidTokenError as e:
        # Catch-all for the remaining pyjwt failure modes (malformed
        # token, missing required claims, etc.). Listed last so the more
        # specific exceptions above shadow it.
        log.warning("rid=%s auth=invalid err=%s", rid, e)
        raise HTTPException(401, "invalid token")
    return {"sub": claims.get("sub"), "aud": claims.get("aud")}


# ---------- case 02: query a relation that may not exist (/audit) ----------
#
# The bug case 02 demonstrates: when migration 002 has not been applied
# on this node, /audit raises psycopg's UndefinedTable and the api
# returns 500. The case's evidence is the per-request log line
# `db=undefined_table err=relation "audit_log" does not exist`, which
# names the missing table cleanly. The proposed fix lives at /healthz
# above (the schema-readiness gate); see also `seed/reset.sh`, which
# rolls schema_migrations back to bootstrap-only between cases.

@app.get("/audit")
def audit(request: Request):
    """Read the most recent 10 audit_log rows. Returns 500 with a clear
    `db=undefined_table` log line when 002_partial.sql has not been
    applied (the case 02 broken state).

    Why catch only UndefinedTable: any other psycopg error reaching this
    handler is unexpected and should bubble up as a true 500 with the
    framework's default handling, not be re-tagged as a db error. Naming
    the specific exception keeps the case's diagnostic signal clean.
    """
    rid = request.state.request_id
    try:
        with db() as conn, conn.cursor() as cur:
            cur.execute("SELECT id, action, created_at FROM audit_log ORDER BY id DESC LIMIT 10")
            rows = cur.fetchall()
    except psycopg.errors.UndefinedTable as e:
        # str(e).strip() because psycopg's UndefinedTable carries a
        # multi-line error context (LINE 1: ... ^ marker etc.); the strip
        # collapses leading/trailing whitespace but keeps the embedded
        # newlines that case 02's reproduce.sh greps for.
        log.error("rid=%s db=undefined_table err=%s", rid, str(e).strip())
        raise HTTPException(500, f"db error: {e}")
    return {"rows": [{"id": r[0], "action": r[1], "created_at": r[2].isoformat()} for r in rows]}


# ---------- case 04: webhook signature verification ----------
#
# Two endpoints: /webhook (broken; case 04's reproduction) and
# /webhook/v2 (fix; pinned by tests/test_webhook.py). The bug class is
# any byte transformation between wire and signature: parsing-then-
# reserializing JSON, BOM stripping, multipart canonicalization, gzip
# decompression. The fix shape is universal — capture the raw body
# bytes first, verify HMAC over them, then parse.

def _compute_sig(secret: str, ts: str, body: bytes) -> str:
    """Compute the v1 HMAC signature over `<timestamp>.<body>`.

    Format mirrors the popular Stripe-style webhook signature scheme:
      - HMAC-SHA256 of `ts.encode() + b"." + body` with `secret`
      - Hex-encoded
      - Prefixed with the version tag `v1=`

    Both /webhook and /webhook/v2 use this helper; the *bug* in /webhook
    is what `body` it passes in, not how the HMAC is computed.
    """
    mac = hmac.new(secret.encode(), digestmod=hashlib.sha256)
    mac.update(ts.encode())
    mac.update(b".")
    mac.update(body)
    return "v1=" + mac.hexdigest()


# Broken-state endpoint kept alive so cases/04-webhook-hmac-reserialization/
# reproduce.sh continues to demonstrate the original bug. The fixed endpoint
# is /webhook/v2 below; pinning tests in tests/test_webhook.py exercise it.
# Do not delete this endpoint without first removing case 04's reproduction.
@app.post("/webhook")
async def webhook(
    request: Request,
    x_signature: str | None = Header(default=None, alias="X-Signature"),
    x_timestamp: str | None = Header(default=None, alias="X-Timestamp"),
):
    """Broken: parses JSON, then computes HMAC over the *re-serialized*
    bytes. Senders that include any whitespace, key reordering, or
    different JSON-library defaults all fail verification because the
    bytes the verifier hashes are not the bytes the sender hashed.
    """
    rid = request.state.request_id

    # BUG: parsing the body before signature check forces FastAPI to consume
    # the request stream; downstream we re-serialize via json.dumps with a
    # specific separator config (no spaces). HMAC over those bytes diverges
    # from the sender's HMAC over the raw bytes whenever the sender's JSON
    # encoder emitted any other shape (e.g. spaces after `:` and `,`).
    parsed: Any = await request.json()
    reserialized = json.dumps(parsed, separators=(",", ":")).encode()

    if not x_signature or not x_timestamp:
        # 400 not 401 because this is a malformed request, not an auth
        # failure. Distinguishes "you forgot to sign" from "your signature
        # is wrong" in the response code; lets the customer's monitoring
        # alert on them differently.
        raise HTTPException(400, "missing signature headers")

    expected = _compute_sig(WEBHOOK_SECRET, x_timestamp, reserialized)
    if not hmac.compare_digest(expected, x_signature):
        # Log the prefix of both signatures and the body length the
        # verifier saw. body_len is the diagnostic field — case 04's
        # writeup shows that a single byte_len delta between sender and
        # receiver claims is the smoking gun for any bytes-transformed-
        # before-hash bug.
        log.warning(
            "rid=%s webhook=signature_mismatch expected_prefix=%s got_prefix=%s body_len=%d",
            rid, expected[:14], x_signature[:14], len(reserialized),
        )
        raise HTTPException(401, "bad signature")

    log.info("rid=%s webhook=accepted event=%s", rid, parsed.get("event"))
    return {"ok": True}


# ---------- case 04 fix: /webhook/v2 verifies before parsing ----------

@app.post("/webhook/v2")
async def webhook_v2(
    request: Request,
    x_signature: str | None = Header(default=None, alias="X-Signature"),
    x_timestamp: str | None = Header(default=None, alias="X-Timestamp"),
):
    """Verify-before-parse: capture the request body as raw bytes,
    compute HMAC over those exact bytes, verify, and only then parse
    JSON. The receiver and the sender are now signing the identical byte
    sequence by construction — no JSON re-serialization in between.
    Lives alongside the broken `/webhook` so case 04 still demonstrates
    the original bug; the pinning tests in tests/test_webhook.py
    exercise this endpoint to guard against a future refactor reverting
    to parse-then-hash.

    Critical implementation detail: `await request.body()` MUST be
    called before `await request.json()`. Once `request.json()` runs,
    the underlying ASGI receive stream is consumed and `request.body()`
    returns `b""`. The order matters and a future refactor that swaps
    the order silently re-introduces the bug.
    """
    rid = request.state.request_id

    if not x_signature or not x_timestamp:
        raise HTTPException(400, "missing signature headers")

    body_raw = await request.body()
    expected = _compute_sig(WEBHOOK_SECRET, x_timestamp, body_raw)
    if not hmac.compare_digest(expected, x_signature):
        log.warning(
            "rid=%s webhook=signature_mismatch expected_prefix=%s got_prefix=%s body_len=%d",
            rid, expected[:14], x_signature[:14], len(body_raw),
        )
        raise HTTPException(401, "bad signature")

    # Parse only AFTER successful verify. The handler's business logic
    # (logging the event type below; in production this is where you'd
    # dispatch to a handler) operates on the parsed dict, but the
    # signature decision was made on the raw bytes, which is the
    # contract this endpoint exists to maintain.
    parsed: Any = json.loads(body_raw)
    log.info("rid=%s endpoint=/webhook/v2 webhook=accepted event=%s",
             rid, parsed.get("event"))
    return {"ok": True}


# ---------- case 05: N+1 endpoint ----------
#
# Two endpoints: /orders (broken N+1) and /orders/v2 (two-query batch
# fix). The case demonstrates that the diagnostic discipline that turns
# a 30-minute ticket into a 30-second one is logging the *query count*
# per request (`queries=N+1` is the smoking gun), independent of any
# wall-clock timing which would be hardware-dependent and flake under CI.

# Broken-state endpoint kept alive so cases/05-endpoint-n-plus-one/
# reproduce.sh continues to demonstrate the N+1 query pattern. The fixed
# endpoint is /orders/v2 below; pinning tests in tests/test_orders.py
# exercise it. Do not delete this endpoint without first removing case
# 05's reproduction.
@app.get("/orders")
def orders(request: Request, limit: int = 50):
    """Broken: one query for the orders page, then one query *per row*
    to look up that row's customer name. Total queries = limit + 1.

    Logs `queries=N` and `dur_ms=X` per request — the queries field is
    the deterministic part the case's pinning would assert against; the
    duration is hardware-dependent and the case README explicitly does
    not pin it.
    """
    rid = request.state.request_id
    out: list[dict] = []
    t0 = time.perf_counter()
    queries = 0
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, customer_id, amount_cents FROM orders ORDER BY id LIMIT %s", (limit,))
        queries += 1
        rows = cur.fetchall()
        for oid, cid, amt in rows:
            # The N+1: one customer lookup per order row. Production-
            # grade analogue: an ORM with lazy='select' relationship
            # accessed inside a serializer (case 05's adjacent-failures
            # subsection lists the most common shapes).
            cur.execute("SELECT name FROM customers WHERE id = %s", (cid,))
            queries += 1
            cust = cur.fetchone()
            out.append({"id": oid, "customer": cust[0] if cust else None, "amount_cents": amt})
    dur_ms = (time.perf_counter() - t0) * 1000.0
    log.info("rid=%s endpoint=/orders rows=%d queries=%d dur_ms=%.1f",
             rid, len(out), queries, dur_ms)
    # The `diag` block is what the reproduce.sh script and the pinning
    # test parse to assert query-count contract. Returning it inside the
    # response (rather than only in the log line) makes the test's
    # assertion mechanical: parse JSON, read diag.queries.
    return {"orders": out, "diag": {"queries": queries, "dur_ms": round(dur_ms, 1)}}


# ---------- case 05 fix: /orders rewritten as two-query batch ----------

@app.get("/orders/v2")
def orders_v2(request: Request, limit: int = 50):
    """The fix for the case-05 N+1: one query for orders, one query for
    the *unique* customer ids in those orders, joined in Python via a
    dict lookup. Query count is bounded at 2 regardless of `limit`.

    Why two queries instead of a JOIN: the customer list is small and
    reused across the page; a JOIN would re-emit the customer name per
    order row, inflating payload size for no benefit. Two queries also
    let the customer-name lookup hit the customers PK index once with
    `id = ANY(%s)` instead of per-row, which is faster on warm caches.

    Lives alongside the broken `/orders` so case 05 still demonstrates
    the bug; the pinning tests in tests/test_orders.py exercise this
    endpoint and would fail immediately if a refactor re-introduces a
    per-row query inside the loop.
    """
    rid = request.state.request_id
    t0 = time.perf_counter()
    queries = 0
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, customer_id, amount_cents FROM orders ORDER BY id LIMIT %s",
            (limit,),
        )
        queries += 1
        rows = cur.fetchall()
        # set comprehension dedups customer ids before the second query
        # so we don't ask the db for the same customer twice when
        # multiple orders share a customer. list() because psycopg's
        # parameter substitution wants a list-or-tuple for ANY().
        ids = list({cid for _, cid, _ in rows})
        if ids:
            cur.execute(
                "SELECT id, name FROM customers WHERE id = ANY(%s)",
                (ids,),
            )
            queries += 1
            names = dict(cur.fetchall())
        else:
            # No orders → no customer lookup needed. Skipping the query
            # is what keeps the query-count contract honest at limit=0
            # (or when no rows match the filter) — otherwise we'd burn
            # one query that can't possibly return data.
            names = {}
        out = [
            {"id": oid, "customer": names.get(cid), "amount_cents": amt}
            for oid, cid, amt in rows
        ]
    dur_ms = (time.perf_counter() - t0) * 1000.0
    log.info(
        "rid=%s endpoint=/orders/v2 rows=%d queries=%d dur_ms=%.1f",
        rid, len(out), queries, dur_ms,
    )
    return {"orders": out, "diag": {"queries": queries, "dur_ms": round(dur_ms, 1)}}


# ---------- /admin/migrate/{step}: operational helper ----------
#
# Used by case 02's reproduce.sh and tests/test_schema_check.py to apply
# a migration on demand. Not a pattern any production deploy should ship
# (executing arbitrary SQL via an http POST is an attacker dream); kept
# here as a convenience because the alternative — copying the migration
# SQL into the test code — duplicates the source of truth for what each
# migration version does.

@app.post("/admin/migrate/{step}")
def admin_migrate(step: int, request: Request):
    """Apply migration `NNN_<slug>.sql` from /migrations/ to the db,
    then record the version in the schema_migrations registry.

    Path resolution: the migrations directory mounted by docker-compose
    holds files like `001_init.sql`, `002_partial.sql`. The handler
    globs for files matching the zero-padded step number, picks the
    lexically-first match (slug-disambiguates if a future migration
    duplicates a number, though it shouldn't). 404 if no match.

    Idempotent re-application: ON CONFLICT (version) DO NOTHING means
    re-running the same migration is safe — useful because reset.sh and
    pinning tests routinely apply 002 multiple times across a single
    pytest run. The SQL itself is also written with `IF NOT EXISTS`
    guards so the DDL doesn't error on re-application; without that the
    registry update and the SQL execution would diverge.
    """
    rid = request.state.request_id
    # Local import keeps the module's import-time cost down — glob is
    # only used by this one handler and isn't worth the import-graph
    # weight at module top.
    import glob
    matches = sorted(glob.glob(f"/migrations/{step:03d}_*.sql"))
    if not matches:
        raise HTTPException(404, f"no migration with step={step} in /migrations/")
    path = matches[0]
    with open(path) as f:
        sql = f.read()
    with db() as conn, conn.cursor() as cur:
        cur.execute(sql)
        # Record the migration in schema_migrations so /healthz?check=schema
        # reflects the new state. ON CONFLICT keeps the call idempotent —
        # re-applying a migration (which is what reset.sh + reproduce sequences
        # routinely do) does not produce a duplicate-key error.
        cur.execute(
            "INSERT INTO schema_migrations (version) VALUES (%s) "
            "ON CONFLICT (version) DO NOTHING",
            (step,),
        )
        conn.commit()
    log.info("rid=%s admin=migrate step=%d", rid, step)
    return {"applied": step}
