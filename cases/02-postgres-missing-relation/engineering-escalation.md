# Escalation: silent migration drift between environments

**Severity:** P2 (recurring failure mode, customer-visible)
**Component:** db deploy pipeline; `api/main.py::audit`
**Triggering ticket:** #4502

## What happened

Migration 002 (creates `audit_log`) ran against production and not against staging. The application has no schema-version awareness, so the api boots, passes its healthcheck, and serves 500s only when the affected endpoint is called.

## Why this got past review

The release process for 002 was: run migration → deploy code that depends on it. That order is correct, but it's enforced only by human-checklist. There's no readiness signal anywhere in the stack that says "I am the code-version that requires schema-version 2; the database I'm connected to is at schema-version 1; I am not ready to serve traffic." The healthcheck answers "is the process up?" not "is the process compatible with what it's connected to?"

The deploy that shipped 002 to prod and missed staging didn't fail any check we have, because we don't have the check. The failure surfaced through customer traffic, which is the wrong path.

## Why this will recur

Every future migration is an opportunity for the same drift. The number of drift opportunities scales with the number of environments × the number of migrations. The current mitigation is "we'll be more careful" which has a known half-life.

## Proposed fix (three small changes that compose)

1. **`schema_migrations` table.** `(version int primary key, applied_at timestamptz default now())`. Migration runner inserts on success. Trivial to add; harder to skip than the absence of one.

2. **Schema-aware healthcheck.** Extend `/healthz` with `?check=schema`. Reads the max version from `schema_migrations`, compares against `EXPECTED_SCHEMA_VERSION` baked into the image at build time. Returns 503 if the database is behind. The image now carries its own statement of "what schema do I require?"

3. **Deploy pipeline gate.** Readiness probe in staging and prod uses `?check=schema`. A node that's behind on migrations fails its probe and never receives traffic. The failure mode flips from "200s most of the time, 500s sometimes" to "the node never goes ready, the deploy alerts, no customer sees it."

(1) and (2) are <100 lines. (3) is a one-line change in the readiness probe config.

## Pinning test that prevents recurrence

`tests/test_schema_check.py::test_schema_check_503s_when_db_is_behind` — boot the api with `EXPECTED_SCHEMA_VERSION=2` against a database where only 001 has been applied, assert `/healthz?check=schema` returns 503 with a body that names the expected vs actual version. Companion `test_schema_check_passes_after_migration_runs` asserts the same probe flips to 200 once `/admin/migrate/2` has run. The pair fails immediately if the schema-version coupling is ever silently dropped from the build (e.g. someone removes the env var, or the runner stops inserting into `schema_migrations`).

## What this does not solve

A migration that runs partially — DDL applied, mid-transaction failure on a non-transactional statement — still leaves a half-state. The schema-migrations table will say version 2 is applied, but the table may be missing columns. That's a separate ticket on migration-runner durability; file it under #4502b. This escalation does not claim to fix that case.
