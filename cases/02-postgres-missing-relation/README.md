# Case 02 — Postgres "relation `audit_log` does not exist" after partial migration

**Ticket:** #4502, P2. `/audit` returning 500 on staging since ~14:10 UTC; prod fine; "we haven't touched the schema."

**Reproduce:** `./reproduce.sh`

## Triage

- 500 body says it: `relation "audit_log" does not exist`. The error message *is* the diagnosis. The question is upstream: why doesn't the table exist *here*.
- Three candidate causes, ranked by base rate from past tickets:
  - migration not applied on this node — most common
  - migration applied then rolled back — possible, leaves a fingerprint in pg logs
  - app talking to the wrong database/schema — failure mode identical, easy to rule out

## What I ran

| Test | Cost | Result | Interpretation |
|------|------|--------|----------------|
| `SELECT current_database(), current_schema()` from the API's connection | 1 query | `app, public` | rules out wrong-db |
| `SELECT MAX(version) FROM schema_migrations` | 1 query | `1` | confirms migration 2 was never applied here; rules out roll-back (which would leave version 2 inserted then deleted, with a fingerprint in pg logs) |
| `\dt` from `psql` inside db | 1 command | `customers`, `orders`, `schema_migrations` — no `audit_log` | corroborates: the table 002 creates is missing |
| `curl /healthz?check=schema` | 1 request | `503 schema behind: expected=2 actual=1` | the readiness check the api exposes for exactly this class of ticket |

Conclusion: migration 002 was never applied on this node.

## Evidence

API log:

```
db=undefined_table err=relation "audit_log" does not exist
```

`schema_migrations` registry inside the db:

```
app=# SELECT * FROM schema_migrations ORDER BY version;
 version |          applied_at
---------+-------------------------------
       1 | 2026-05-09 17:10:06.518858+00
(1 row)
```

`/healthz?check=schema` from the api host: `503 schema behind: expected=2 actual=1`.

## Why it's reproducible here

The lab applies `001_init.sql` automatically via `docker-entrypoint-initdb.d`; that bootstrap inserts version 1 into `schema_migrations`. `002_partial.sql` is *not* mounted there — it's applied only via `POST /admin/migrate/2`, which runs the SQL and then inserts version 2 into `schema_migrations`. Real-world analogue: the deploy pipeline ran the bootstrap migration then skipped the v2 step; or a replica was provisioned from a snapshot taken before 002 merged.

## The interesting part

The lab ships with a `schema_migrations` registry (added in 001 and updated by the migration runner), so the diagnostic for "did this migration land here?" is one `SELECT MAX(version)`. The harder version of this ticket is the one that arrives *without* a migrations table — a system that grew up without a migration tool, where the only way to compare environments is a hand-diff of `\dt` output across hosts. That's the state most production support tickets of this class file from. The lab demonstrates the post-fix shape (registry present + `/healthz?check=schema` readiness gate); the case writeup carries the diagnostic muscle for the harder pre-fix shape too.

## Fix

- **Now:** `curl -sS -X POST http://localhost:8000/admin/migrate/2` — applies 002 on this node and inserts the registry row. `/audit` returns rows; `/healthz?check=schema` flips green.
- **This sprint (already shipped in this lab):** the `schema_migrations(version int pk, applied_at timestamptz)` registry, the migration runner that updates it, and the `/healthz?check=schema` readiness probe baked into the image with `EXPECTED_SCHEMA_VERSION`.
- **The deploy-pipeline gate:** the readiness probe in staging/prod must use `?check=schema`. A node behind on migrations fails its probe and never receives traffic — the failure mode flips from "200s most of the time, 500s sometimes on the affected endpoint" to "the node never goes ready, the deploy alerts, no customer sees it."

After the gate is wired into deploys, the next "did this migration land here?" ticket never files because the node never serves traffic.

## Adjacent failure modes (not hit in this case, but the same pattern)

- **Column missing because a migration partially applied.** DDL succeeded but a follow-up DML (backfill, default-value population) failed mid-way and left the schema in a half-state. `schema_migrations` says "version 2 applied" because the runner saw the DDL succeed; the column is there but the rows the app expects to populate aren't, and the failure surfaces as `NULL` where the code assumes a value. Fix this class of bug by wrapping migrations in a transaction *or* by treating each DML step as its own version.
- **Replica lag.** Writes go to the primary, this query went to a read replica that's seconds behind. The schema is identical; the data isn't. Hard to diagnose because `\dt` from any one connection looks fine. The signal is `pg_stat_replication` lag bytes against the replica the failing connection landed on.
- **`search_path` drift.** The `audit_log` table exists, but in a schema (`reporting`, `legacy`, etc.) that isn't on the connection's `search_path`. Same error message — "relation does not exist." Diagnostic: `SHOW search_path;` and `SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON c.relnamespace = n.oid WHERE c.relname = 'audit_log';` together name the schema vs path mismatch.
