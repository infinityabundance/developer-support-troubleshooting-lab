"""Generates colab/run_lab.ipynb from a structured cell list.

This script runs once locally (not in Colab) to produce the notebook
JSON. The notebook itself runs on Colab. Keeping the cell content as
Python strings here is easier to read and edit than hand-writing
8KB of JSON.

Usage:  python3 colab/build_notebook.py
Output: colab/run_lab.ipynb
"""
import json
from pathlib import Path


def md(*lines: str) -> dict:
    """Build a markdown cell from a sequence of source lines (no trailing
    newlines required — added here)."""
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": [line + ("\n" if not line.endswith("\n") else "") for line in lines][:-1] +
                  [lines[-1]],  # last line has no trailing newline
    }


def code(*lines: str) -> dict:
    """Build a code cell from a sequence of source lines."""
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": [line + ("\n" if not line.endswith("\n") else "") for line in lines][:-1] +
                  [lines[-1]],
    }


CELLS = [
    # ============================================================
    # 1. Title + intro
    # ============================================================
    md(
        "# Developer Support Troubleshooting Lab — Colab walkthrough",
        "",
        "[Repo on GitHub](https://github.com/infinityabundance/developer-support-troubleshooting-lab)",
        "",
        "This notebook runs the lab end-to-end on Colab's free-tier Ubuntu VM in about five minutes. The lab is a deliberately-broken FastAPI + Postgres platform with seven diagnostic cases (auth, database, container networking, webhook integration, performance, Linux/DNS, TLS); each case ships with a reproduction, captured logs, the customer response, the engineering escalation, the fix, and a pinning test that fails when the fix is reverted.",
        "",
        "**What this notebook does:**",
        "1. Installs Postgres natively (Colab has no Docker runtime).",
        "2. Bootstraps the database, launches the FastAPI service.",
        "3. Walks through each case: shows the bug, shows the fix, runs the pinning test.",
        "4. Cases 03 and 06 are intrinsically Docker-dependent and are presented as skipped cells with an explanation.",
        "",
        "**To run it:** Runtime → Run all (or click the ► button on each cell in order).",
        "",
        "Every code cell is preceded by a markdown cell explaining what's about to happen and what the expected output looks like. The case-by-case writeups in the repo (`cases/NN-*/README.md`) carry the deeper diagnostic narrative; this notebook is the runnable demonstration.",
    ),

    # ============================================================
    # 2. Setup: clone repo
    # ============================================================
    md(
        "## Setup 1 of 4 — Clone or refresh the repo",
        "",
        "Pulls the repo into the Colab VM and changes into it. If the directory already exists from an earlier run, this refreshes the throwaway Colab checkout to current `origin/main` so rerunning cells cannot keep using stale test code.",
    ),
    code(
        "import os",
        "import subprocess",
        "from pathlib import Path",
        "",
        "repo_url = 'https://github.com/infinityabundance/developer-support-troubleshooting-lab.git'",
        "repo_dir = Path('developer-support-troubleshooting-lab')",
        "base_dir = Path('/content') if Path('/content').exists() else Path.cwd()",
        "os.chdir(base_dir)",
        "",
        "if (repo_dir / '.git').exists():",
        "    subprocess.run(['git', '-C', str(repo_dir), 'fetch', '--depth', '1',",
        "                    'origin', 'main:refs/remotes/origin/main'], check=True)",
        "    subprocess.run(['git', '-C', str(repo_dir), 'checkout', '-B', 'main',",
        "                    'origin/main'], check=True)",
        "else:",
        "    subprocess.run(['git', 'clone', '--depth', '1', '--branch', 'main',",
        "                    repo_url, str(repo_dir)], check=True)",
        "",
        "os.chdir(repo_dir)",
        "head = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], text=True).strip()",
        "print(f'using repo checkout {head}')",
    ),

    # ============================================================
    # 3. Setup: install postgres
    # ============================================================
    md(
        "## Setup 2 of 4 — Install and start PostgreSQL",
        "",
        "The lab normally runs Postgres as a Docker container via `docker compose`. Colab can't do Docker, so we install Postgres natively via apt and start the service. Then we create or update the `app` user + `app` database and run `db/migrations/001_init.sql` against a freshly reset schema — the same SQL the postgres image's `/docker-entrypoint-initdb.d` hook would run on first boot.",
        "",
        "After this cell:",
        "- `postgres` service is running on `localhost:5432`",
        "- `app` user exists with password `app`",
        "- `app` database exists, owned by `app`",
        "- `customers`, `orders`, and `schema_migrations` tables are populated; `schema_migrations` holds version 1 only (the case-02 broken state we'll demonstrate later)",
    ),
    code(
        "import os, subprocess",
        "",
        "# Install postgres (suppressed output to keep the cell tidy).",
        "!apt-get -qq update > /dev/null",
        "!DEBIAN_FRONTEND=noninteractive apt-get -qq install -y postgresql postgresql-contrib > /dev/null",
        "!service postgresql start",
        "",
        "# Create or update the app role. SUPERUSER so the user can run schema",
        "# changes via /admin/migrate later (matches docker-compose behavior).",
        "subprocess.run(['sudo', '-u', 'postgres', 'psql', '-v', 'ON_ERROR_STOP=1', '-c', r\"\"\"",
        "DO $$",
        "BEGIN",
        "  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app') THEN",
        "    CREATE ROLE app LOGIN PASSWORD 'app' SUPERUSER;",
        "  ELSE",
        "    ALTER ROLE app WITH LOGIN PASSWORD 'app' SUPERUSER;",
        "  END IF;",
        "END",
        "$$;",
        "\"\"\"], check=True)",
        "",
        "# Create the app database if needed; reruns keep the same database.",
        "exists = subprocess.run(['sudo', '-u', 'postgres', 'psql', '-tAc',",
        "    \"SELECT 1 FROM pg_database WHERE datname = 'app'\"],",
        "    check=True, capture_output=True, text=True).stdout.strip()",
        "if exists != '1':",
        "    subprocess.run(['sudo', '-u', 'postgres', 'createdb', '-O', 'app', 'app'], check=True)",
        "else:",
        "    subprocess.run(['sudo', '-u', 'postgres', 'psql', '-v', 'ON_ERROR_STOP=1', '-c',",
        "        'ALTER DATABASE app OWNER TO app;'], check=True)",
        "",
        "# Reset the public schema so this cell is safe to rerun and always",
        "# returns the lab to the case-02 baseline: migration 001 applied,",
        "# migration 002 absent.",
        "subprocess.run(['sudo', '-u', 'postgres', 'psql', '-d', 'app', '-v',",
        "    'ON_ERROR_STOP=1', '-c',",
        "    'DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public AUTHORIZATION app; GRANT ALL ON SCHEMA public TO app;'],",
        "    check=True)",
        "",
        "# Bootstrap the schema. This runs the same SQL as the postgres image's",
        "# initdb hook in docker-compose.yml.",
        "env = {**os.environ, 'PGPASSWORD': 'app'}",
        "subprocess.run(['psql', '-h', 'localhost', '-U', 'app', '-d', 'app',",
        "    '-v', 'ON_ERROR_STOP=1', '-f', 'db/migrations/001_init.sql'],",
        "    env=env, check=True)",
        "",
        "# Confirm the bootstrap state.",
        "print('=== \\\\dt ===')",
        "subprocess.run(['sudo', '-u', 'postgres', 'psql', '-d', 'app', '-c', '\\\\dt'])",
        "print('=== schema_migrations ===')",
        "subprocess.run(['sudo', '-u', 'postgres', 'psql', '-d', 'app', '-c',",
        "    'SELECT * FROM schema_migrations;'])",
    ),

    # ============================================================
    # 4. Setup: pip install
    # ============================================================
    md(
        "## Setup 3 of 4 — Install Python dependencies",
        "",
        "Colab already carries packages like Gradio, MCP, and Google ADK in the shared Python environment. Installing the production Docker pins from `api/requirements.txt` here can downgrade FastAPI, Starlette, PyJWT, or Uvicorn below what those preinstalled packages require, which produces noisy dependency-conflict warnings.",
        "",
        "So the notebook installs a Colab-compatible host dependency set instead. The production container still uses `api/requirements.txt`; this cell is only for Colab's shared runtime.",
    ),
    code(
        "!pip install -q \\",
        "  'fastapi>=0.115.2,<1.0' \\",
        "  'starlette>=0.40,<1.0' \\",
        "  'uvicorn[standard]>=0.34,<1.0' \\",
        "  'pyjwt>=2.10.1,<3' \\",
        "  'psycopg[binary]>=3.2,<4' \\",
        "  'sqlalchemy>=2.0,<3' \\",
        "  'httpx>=0.27,<0.30' \\",
        "  'pytest>=8.3,<10' \\",
        "  'pytest-rerunfailures>=15,<16'",
    ),

    # ============================================================
    # 5. Setup: launch api
    # ============================================================
    md(
        "## Setup 4 of 4 — Launch the FastAPI service",
        "",
        "Sets the env vars `docker-compose.yml` normally injects, then launches `uvicorn` as a background subprocess. Logs go to `/tmp/api.log`; we'll `tail` that file in case-by-case cells to surface the per-request log lines.",
        "",
        "The `/migrations` symlink mirrors the Docker volume mount that lets the api's `POST /admin/migrate/{step}` handler read SQL files at runtime.",
        "",
        "After this cell, `http://localhost:8000/healthz` returns 200.",
    ),
    code(
        "import os, subprocess, time",
        "import requests",
        "",
        "# If this notebook cell is rerun, stop the previous uvicorn process",
        "# first so the new checkout's code is what serves port 8000.",
        "old_api_proc = globals().get('api_proc')",
        "if old_api_proc is not None and old_api_proc.poll() is None:",
        "    old_api_proc.terminate()",
        "    try:",
        "        old_api_proc.wait(timeout=5)",
        "    except subprocess.TimeoutExpired:",
        "        old_api_proc.kill()",
        "        old_api_proc.wait(timeout=5)",
        "",
        "# Env the api expects (mirrors docker-compose.yml's environment: block).",
        "os.environ.update({",
        "    'JWT_SECRET': 'dev-secret-do-not-use-in-prod-32bytes',",
        "    'JWT_AUDIENCE': 'api',",
        "    'WEBHOOK_SECRET': 'whsec_devonly',",
        "    'DATABASE_URL': 'postgresql://app:app@localhost:5432/app',",
        "    'EXPECTED_SCHEMA_VERSION': '2',",
        "    'BIND_HOST': '0.0.0.0',",
        "    'BIND_PORT': '8000',",
        "})",
        "",
        "# Mirror the docker-compose volume mount that exposes the migrations dir",
        "# at /migrations inside the api container. /admin/migrate/{step} reads",
        "# from there.",
        "if not os.path.exists('/migrations'):",
        "    os.symlink(os.path.abspath('db/migrations'), '/migrations')",
        "",
        "# Launch uvicorn in the background. cwd=api so `uvicorn main:app` finds",
        "# main.py and the log_config.json (referenced as a relative path).",
        "api_log = open('/tmp/api.log', 'w')",
        "api_proc = subprocess.Popen(",
        "    ['uvicorn', 'main:app', '--host', '0.0.0.0', '--port', '8000',",
        "     '--log-config', 'log_config.json'],",
        "    cwd='api',",
        "    stdout=api_log, stderr=subprocess.STDOUT,",
        ")",
        "",
        "# Wait up to 30s for the api to come up.",
        "for i in range(30):",
        "    try:",
        "        r = requests.get('http://localhost:8000/healthz', timeout=1)",
        "        if r.status_code == 200:",
        "            print(f'api up (PID {api_proc.pid}) after {i+1}s')",
        "            break",
        "    except requests.exceptions.RequestException:",
        "        pass",
        "    time.sleep(1)",
        "else:",
        "    print('api did not become healthy; tail of /tmp/api.log:')",
        "    print(open('/tmp/api.log').read())",
    ),

    # ============================================================
    # 6. Verify platform
    # ============================================================
    md(
        "## Quick platform check",
        "",
        "Two requests:",
        "- `GET /healthz` should return `{'ok': True}` (db reachable, no schema check).",
        "- `GET /healthz?check=schema` should return **503 with `schema behind: expected=2 actual=1`**, because the lab is intentionally in the case-02 broken state — only migration 001 has been applied; the api expects version 2.",
        "",
        "This 503 isn't a bug; it's the readiness gate working correctly. Case 02 below walks through it.",
    ),
    code(
        "print('GET /healthz:')",
        "r = requests.get('http://localhost:8000/healthz')",
        "print(f'  status: {r.status_code}')",
        "print(f'  body:   {r.json()}')",
        "",
        "print()",
        "print('GET /healthz?check=schema (expect 503 — case-02 broken state):')",
        "r = requests.get('http://localhost:8000/healthz', params={'check': 'schema'})",
        "print(f'  status: {r.status_code}')",
        "print(f'  body:   {r.text}')",
    ),

    # ============================================================
    # 7. Case 01
    # ============================================================
    md(
        "---",
        "",
        "## Case 01 — JWT audience-claim mismatch (`/me`)",
        "",
        "**The bug:** the verifier is configured for a single audience (`api`), but the customer's IdP issues tokens with `aud=api-staging`. Signature is valid, expiry is fine, only the audience claim mismatches → 401. The customer sees a confusing rejection because everything else about the token is right.",
        "",
        "**What we'll do:**",
        "1. Mint a JWT with `aud=api-staging` using the dev secret.",
        "2. POST it to `/me`, observe the 401 + the `auth=invalid_audience` log line (the smoking-gun field is `expected=` which names the verifier's accept-list).",
        "3. Run `tests/test_auth.py` against the live api — the pinning tests verify the post-fix code (verifier accepts a list of audiences) and would fail immediately if the fix were reverted to single-string handling. Critically, the test mints with audience values that are NOT derived from the production env — that's the symmetry break that prevents the original bug from re-shipping.",
        "",
        "Deeper writeup: [`cases/01-jwt-audience-mismatch/README.md`](cases/01-jwt-audience-mismatch/README.md).",
    ),
    code(
        "import jwt as pyjwt  # the pyjwt package",
        "",
        "# Mint a token with the WRONG audience. Same secret the api uses.",
        "token = pyjwt.encode(",
        "    {'sub': 'user-42', 'aud': 'api-staging'},",
        "    'dev-secret-do-not-use-in-prod-32bytes',",
        "    algorithm='HS256',",
        ")",
        "",
        "r = requests.get(",
        "    'http://localhost:8000/me',",
        "    headers={'Authorization': f'Bearer {token}'},",
        ")",
        "print(f'GET /me with aud=api-staging:')",
        "print(f'  status: {r.status_code}  (expected: 401)')",
        "print(f'  body:   {r.text}')",
        "",
        "# The smoking gun is on the api's log line. Pull the most recent",
        "# auth=invalid_audience entry from /tmp/api.log.",
        "print()",
        "print('api log (most recent invalid_audience line):')",
        "!grep auth=invalid_audience /tmp/api.log | tail -n 1",
    ),
    md(
        "Now run the pinning tests. These use FastAPI's `TestClient` against the api code (in-process, doesn't need our running uvicorn) with a hardcoded multi-audience config — the symmetry break that prevents test/prod config drift from hiding the bug.",
    ),
    code(
        "!pytest -q tests/test_auth.py",
    ),

    # ============================================================
    # 8. Case 02
    # ============================================================
    md(
        "---",
        "",
        "## Case 02 — Postgres `relation \"audit_log\" does not exist` (partial migration)",
        "",
        "**The bug:** migration 002 was applied to production but skipped on staging. `/audit` queries the `audit_log` table that 002 creates; on the affected node the table doesn't exist, the api returns 500. The bare `/healthz` keeps returning 200 because the db is reachable — the missing migration is invisible to a liveness probe.",
        "",
        "**What we'll do:**",
        "1. Hit `/audit`, observe the 500 + the `db=undefined_table` log line.",
        "2. Show `/healthz?check=schema` returning 503 (the readiness probe that *would* have caught this).",
        "3. Apply migration 002 via `POST /admin/migrate/2`. The migration runner inserts version 2 into `schema_migrations`.",
        "4. Hit `/audit` again, observe 200.",
        "5. Hit `/healthz?check=schema` again, observe 200.",
        "6. Run the pinning tests.",
        "",
        "Deeper writeup: [`cases/02-postgres-missing-relation/README.md`](cases/02-postgres-missing-relation/README.md).",
    ),
    code(
        "# Step 1: hit /audit in the broken state.",
        "r = requests.get('http://localhost:8000/audit')",
        "print(f'GET /audit (broken state):')",
        "print(f'  status: {r.status_code}  (expected: 500)')",
        "print(f'  body:   {r.text[:200]}')",
        "",
        "print()",
        "print('api log (most recent undefined_table line):')",
        "!grep db=undefined_table /tmp/api.log | tail -n 1",
        "",
        "print()",
        "# Step 2: show the readiness probe is correctly red.",
        "r = requests.get('http://localhost:8000/healthz', params={'check': 'schema'})",
        "print(f'GET /healthz?check=schema (broken state):')",
        "print(f'  status: {r.status_code}  body: {r.text}')",
        "",
        "print()",
        "# Step 3: apply migration 002.",
        "r = requests.post('http://localhost:8000/admin/migrate/2')",
        "print(f'POST /admin/migrate/2:')",
        "print(f'  status: {r.status_code}  body: {r.text}')",
        "",
        "print()",
        "# Step 4: re-hit /audit; now returns 200 with rows.",
        "r = requests.get('http://localhost:8000/audit')",
        "print(f'GET /audit (after fix):')",
        "print(f'  status: {r.status_code}  (expected: 200)')",
        "print(f'  body:   {r.json()}')",
        "",
        "print()",
        "# Step 5: re-hit the schema check; now green.",
        "r = requests.get('http://localhost:8000/healthz', params={'check': 'schema'})",
        "print(f'GET /healthz?check=schema (after fix):')",
        "print(f'  status: {r.status_code}  body: {r.text}')",
    ),
    md(
        "Pinning tests for case 02 live in `tests/test_schema_check.py`. They reset the db to the broken state per-test (via a fixture that deletes `schema_migrations` rows > 1 and drops `audit_log`) and verify the schema-check + migration-runner contract end-to-end.",
    ),
    code(
        "!pytest -q tests/test_schema_check.py",
    ),

    # ============================================================
    # 9. Case 03 SKIPPED
    # ============================================================
    md(
        "---",
        "",
        "## Case 03 — Container reachable from inside, refused from host (SKIPPED on Colab)",
        "",
        "Case 03 demonstrates a Docker container binding to its own loopback interface (`127.0.0.1` inside the container, which is *not* the host's loopback). The in-container healthcheck passes; the host curl on the published port fails with connection refused.",
        "",
        "This is intrinsically a Docker-runtime failure mode — without a Docker container, there is no \"container's loopback vs host's loopback\" distinction to demonstrate. Colab does not provide a Docker runtime, so this case can't run here.",
        "",
        "**To see this case live:** clone the repo locally, run `make up && make reproduce-03`. The case's full writeup, including the captured `ss -tulpn` output that names `127.0.0.1:8000` as the smoking gun, is in [`cases/03-container-bind-127001/README.md`](cases/03-container-bind-127001/README.md). The pinning test (`tests/test_bind.py`) parses `docker-compose.yml` to assert the default bind is `0.0.0.0`, plus an integration-shaped check from outside the container.",
    ),

    # ============================================================
    # 10. Case 04
    # ============================================================
    md(
        "---",
        "",
        "## Case 04 — Webhook HMAC fails after JSON re-serialization (`/webhook`)",
        "",
        "**The bug:** the webhook handler calls `await request.json()` *before* computing the HMAC, then signs `json.dumps(parsed, separators=(\",\",\":\"))`. Senders that include any whitespace in their payload — which most JSON encoders do by default — sign the raw bytes; the verifier signs the re-serialized (no-whitespace) bytes; the two HMACs diverge → 401.",
        "",
        "**The fix** lives at `/webhook/v2`: capture `await request.body()` first, verify HMAC over those raw bytes, then parse JSON.",
        "",
        "**What we'll do:**",
        "1. Build a payload with whitespace, sign it, POST to `/webhook` (broken) → 401.",
        "2. POST the same payload + signature to `/webhook/v2` (fixed) → 200.",
        "3. Run the pinning tests including the symmetry-break test that catches a parse-then-hash regression.",
        "",
        "Deeper writeup: [`cases/04-webhook-hmac-reserialization/README.md`](cases/04-webhook-hmac-reserialization/README.md).",
    ),
    code(
        "import hashlib, hmac, time",
        "",
        "secret = 'whsec_devonly'  # matches WEBHOOK_SECRET env",
        "ts = str(int(time.time()))",
        "",
        "# Payload with whitespace — what a real sender's JSON encoder emits.",
        "payload_with_spaces = '{\"event\": \"order.placed\", \"id\": 42}'.encode()",
        "",
        "# Sign over the EXACT bytes we'll send on the wire.",
        "mac = hmac.new(secret.encode(), digestmod=hashlib.sha256)",
        "mac.update(ts.encode()); mac.update(b'.'); mac.update(payload_with_spaces)",
        "sig = 'v1=' + mac.hexdigest()",
        "",
        "headers = {",
        "    'Content-Type': 'application/json',",
        "    'X-Timestamp': ts,",
        "    'X-Signature': sig,",
        "}",
        "",
        "# Step 1: POST to broken endpoint.",
        "r = requests.post('http://localhost:8000/webhook', data=payload_with_spaces, headers=headers)",
        "print(f'POST /webhook (broken):')",
        "print(f'  status: {r.status_code}  (expected: 401)')",
        "print(f'  body:   {r.text}')",
        "",
        "print()",
        "print('api log (most recent signature_mismatch line):')",
        "!grep webhook=signature_mismatch /tmp/api.log | tail -n 1",
        "",
        "print()",
        "# Step 2: POST to fixed endpoint. Same payload, same signature.",
        "r = requests.post('http://localhost:8000/webhook/v2', data=payload_with_spaces, headers=headers)",
        "print(f'POST /webhook/v2 (fixed):')",
        "print(f'  status: {r.status_code}  (expected: 200)')",
        "print(f'  body:   {r.text}')",
    ),
    code(
        "!pytest -q tests/test_webhook.py",
    ),

    # ============================================================
    # 11. Case 05
    # ============================================================
    md(
        "---",
        "",
        "## Case 05 — `/orders` p99 spikes from N+1 query pattern",
        "",
        "**The bug:** `/orders` issues one query for the order page, then one query *per row* to look up that row's customer name. With `limit=200` that's 201 queries; under load the per-request latency tracks the round-trip count, not the per-query cost.",
        "",
        "**The fix** lives at `/orders/v2`: one query for orders, one query for the unique customer ids, joined in Python via dict lookup. Query count is bounded at 2 regardless of `limit`.",
        "",
        "**What we'll do:**",
        "1. Hit `/orders?limit=200`, parse the diag block, observe `queries=201`.",
        "2. Hit `/orders/v2?limit=200`, observe `queries=2`.",
        "3. Run the pinning tests, parametrized over `limit ∈ {1, 10, 50, 200}` to verify the bound holds.",
        "",
        "Deeper writeup: [`cases/05-endpoint-n-plus-one/README.md`](cases/05-endpoint-n-plus-one/README.md).",
    ),
    code(
        "r = requests.get('http://localhost:8000/orders', params={'limit': 200})",
        "print(f'GET /orders?limit=200 (broken N+1):')",
        "diag = r.json()['diag']",
        "print(f'  status:    {r.status_code}')",
        "print(f'  rows:      {len(r.json()[\"orders\"])}')",
        "print(f'  queries:   {diag[\"queries\"]}  (expected: 201 = 1 + limit)')",
        "print(f'  dur_ms:    {diag[\"dur_ms\"]}')",
        "",
        "print()",
        "r = requests.get('http://localhost:8000/orders/v2', params={'limit': 200})",
        "print(f'GET /orders/v2?limit=200 (fixed):')",
        "diag = r.json()['diag']",
        "print(f'  status:    {r.status_code}')",
        "print(f'  rows:      {len(r.json()[\"orders\"])}')",
        "print(f'  queries:   {diag[\"queries\"]}  (expected: 2 regardless of limit)')",
        "print(f'  dur_ms:    {diag[\"dur_ms\"]}')",
    ),
    code(
        "!pytest -q tests/test_orders.py",
    ),

    # ============================================================
    # 12. Case 06 SKIPPED
    # ============================================================
    md(
        "---",
        "",
        "## Case 06 — Intermittent DNS in Alpine container (`ndots`/musl) (SKIPPED on Colab)",
        "",
        "Case 06 demonstrates the interaction between Alpine's musl resolver, Docker's embedded DNS at `127.0.0.11`, and the `ndots:0` directive Docker writes into the container's `/etc/resolv.conf`. The bug only manifests when *all three* are present — without Docker, there's no embedded DNS at `127.0.0.11` and the resolver behaves differently. Colab can't replicate either side of that intersection.",
        "",
        "**To see this case live:** clone the repo locally, run `make up && make reproduce-06`. The captured side-by-side `getent`/`dig` output that makes the bug visible is in [`cases/06-dns-ndots-musl/README.md`](cases/06-dns-ndots-musl/README.md), along with the runtime-portability note explaining why the bug doesn't reproduce on Podman either (Podman uses aardvark-dns at the network gateway instead of an embedded resolver).",
    ),

    # ============================================================
    # 13. Case 07
    # ============================================================
    md(
        "---",
        "",
        "## Case 07 — TLS handshake fails: server presents leaf only, intermediate missing",
        "",
        "**The bug:** the server's loaded cert file contains only the leaf certificate; the intermediate is on disk but not concatenated in. Clients whose trust store has the root but not the intermediate cannot bridge the chain → `unable to get local issuer certificate` (curl exit 60). Clients with the intermediate cached from an unrelated session validate locally and succeed — that's where the customer's \"works for half my fleet\" framing comes from.",
        "",
        "Case 07 is **already self-contained Python** — it generates its own throwaway cert chain in a tempdir and runs a tiny TLS server (`tls_server.py`). No Docker dependency. So we just run the existing `reproduce.sh` directly.",
        "",
        "Expected output:",
        "- `before_fix_exit=60` — leaf-only chain, curl can't validate.",
        "- `after_fix_exit=0` — fullchain (leaf + intermediate), curl returns 200.",
        "",
        "Deeper writeup: [`cases/07-tls-incomplete-chain/README.md`](cases/07-tls-incomplete-chain/README.md).",
    ),
    code(
        "!bash cases/07-tls-incomplete-chain/reproduce.sh",
    ),
    md(
        "The pinning tests in `tests/test_cert_chain.py` cover both the boot-time chain-completeness gate (the server refuses to start with a leaf-only cert) and the from-the-wire validation (a curl with a cold trust store gets 200 against a fullchain server, exit 60 against a leaf-only server).",
    ),
    code(
        "!pytest -q tests/test_cert_chain.py",
    ),

    # ============================================================
    # 14. Full pinning suite
    # ============================================================
    md(
        "---",
        "",
        "## Full pinning suite",
        "",
        "Runs every pinning test together. Skips `tests/test_reproductions.py` because most of its cases shell out to `docker compose` — case 07 is the only one we already exercised inline above.",
        "",
        "Each test in this suite was verified locally to fail when the corresponding fix is reverted — three independent revert experiments are on record (case 02 schema_migrations INSERT removal, case 07 chain-completeness gate disable, case 04 raw-bytes capture revert).",
    ),
    code(
        "!pytest -q tests/test_auth.py tests/test_bind.py tests/test_orders.py tests/test_cert_chain.py tests/test_schema_check.py tests/test_trace.py tests/test_webhook.py",
    ),

    # ============================================================
    # 15. Closing
    # ============================================================
    md(
        "---",
        "",
        "## What just ran (recap)",
        "",
        "| Case | Layer | Demonstrated |",
        "|---|---|---|",
        "| 01 | Auth | JWT audience mismatch + multi-audience fix |",
        "| 02 | Database | Migration drift + schema-aware readiness probe |",
        "| 03 | Network | *(skipped — needs Docker container loopback)* |",
        "| 04 | Integration | HMAC over re-serialized JSON + verify-before-parse fix |",
        "| 05 | Performance | N+1 query + bounded two-query batch fix |",
        "| 06 | Linux/DNS | *(skipped — needs Docker embedded DNS + Alpine)* |",
        "| 07 | TLS | Incomplete chain + boot-time gate + cold-trust-store test |",
        "",
        "Five cases ran end-to-end; every fix is pinned by a test that fails when the fix is reverted. Two cases are documented above with a clear explanation of why they need a Docker runtime and a pointer to the local-install path for seeing them live.",
        "",
        "## Where to next",
        "",
        "- The first-30-seconds triage discipline: [`TRIAGE.md`](TRIAGE.md). Steps 1–7 are the mechanical sequence; steps 8–11 are the political/judgment layer (when to push back on a customer's framing, when to refuse a ticket, when to escalate, when a writeup leaves the queue).",
        "- The single-document casebook: [`docs/support-casebook.md`](docs/support-casebook.md). All seven cases in postmortem format, with a preface section on the symmetry trap (the meta-pattern that recurs in cases 01, 04, 07).",
        "- The cross-service request tracer: `bin/trace.sh`. Greps the api + db log streams for a single request id, prints time-ordered. See case 04's README for a worked example.",
        "- The full local install: clone the repo, `make up && make reproduce-all && pytest`. That covers cases 03 and 06 too.",
        "",
        "Repo: <https://github.com/infinityabundance/developer-support-troubleshooting-lab>",
    ),
]


# Wrap and write.
notebook = {
    "cells": CELLS,
    "metadata": {
        "kernelspec": {
            "display_name": "Python 3",
            "language": "python",
            "name": "python3",
        },
        "language_info": {"name": "python"},
        "colab": {"provenance": []},
    },
    "nbformat": 4,
    "nbformat_minor": 4,
}

out = Path(__file__).parent / "run_lab.ipynb"
out.write_text(json.dumps(notebook, indent=1))
print(f"wrote {out} — {len(CELLS)} cells, {out.stat().st_size} bytes")
