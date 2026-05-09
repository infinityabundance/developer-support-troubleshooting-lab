# Top-level orchestration for the lab. Each target is a thin wrapper
# around either `docker compose` or a script under cases/, seed/, or
# bin/. The Makefile is the documented entry point used by the README's
# Quickstart section; it also gates CI's run sequence.

.PHONY: up down reset logs ps shell-api shell-db trace \
        reproduce-01 reproduce-02 reproduce-03 reproduce-04 reproduce-05 reproduce-06 reproduce-07 \
        reproduce-all test lint clean

# Override at the command line if needed (e.g. COMPOSE="podman-compose"
# for podman). Defaults to the docker CLI's built-in compose subcommand.
COMPOSE := docker compose

# Bring the stack up and wait until the api's published port is
# answering /healthz from the host. The compose healthcheck only
# verifies the in-container reachability; this loop verifies the
# from-host reachability that all reproduce.sh scripts depend on.
# 30s timeout = 30 × 1s; first-time runs that need to pull base
# images can take longer, in which case re-run `make up`.
up:
	$(COMPOSE) up -d --build
	@echo "waiting for api healthcheck..."
	@for i in $$(seq 1 30); do \
	  if curl -sf http://localhost:8000/healthz > /dev/null; then \
	    echo "api up"; exit 0; fi; \
	  sleep 1; \
	done; \
	echo "api did not become healthy"; exit 1

# `down -v` drops the db volume so the next `up` re-runs the
# /docker-entrypoint-initdb.d hook (which loads 001_init.sql). Without
# -v, db state from the previous run persists and 001 doesn't re-run,
# which would leave any test-injected schema_migrations rows around.
down:
	$(COMPOSE) down -v

# Reset between cases without bringing the stack down. seed/reset.sh
# undoes per-case mutations (audit_log, idx_orders_customer_id,
# schema_migrations rows > 1, BIND_HOST overrides) and restarts the
# api with default env. Idempotent and faster than `down + up`.
reset:
	./seed/reset.sh

# Last 200 lines of every service's logs, useful for ad-hoc debugging.
logs:
	$(COMPOSE) logs --tail=200

ps:
	$(COMPOSE) ps

# Interactive shells into running containers.
shell-api:
	$(COMPOSE) exec api /bin/sh

shell-db:
	$(COMPOSE) exec db psql -U app -d app

# Trace a single request across api + db logs, time-ordered.
# Usage: make trace REQUEST=abc123def456
#
# Delegates to bin/trace.sh; the COMPOSE env-var passthrough lets the
# script use the same compose CLI that the rest of the Makefile uses.
# REQUEST is required; the early-exit prints a usage hint if missing.
trace:
	@if [ -z "$(REQUEST)" ]; then \
	  echo "usage: make trace REQUEST=<request-id>"; exit 2; \
	fi
	@COMPOSE="$(COMPOSE)" ./bin/trace.sh "$(REQUEST)"

# Per-case reproduction targets. Each delegates to the corresponding
# reproduce.sh, which is idempotent (calls seed/reset.sh first).
# Listed individually rather than via a pattern rule because the
# script paths don't follow a clean make-pattern.
reproduce-01: ; ./cases/01-jwt-audience-mismatch/reproduce.sh
reproduce-02: ; ./cases/02-postgres-missing-relation/reproduce.sh
reproduce-03: ; ./cases/03-container-bind-127001/reproduce.sh
reproduce-04: ; ./cases/04-webhook-hmac-reserialization/reproduce.sh
reproduce-05: ; ./cases/05-endpoint-n-plus-one/reproduce.sh
reproduce-06: ; ./cases/06-dns-ndots-musl/reproduce.sh
reproduce-07: ; ./cases/07-tls-incomplete-chain/reproduce.sh

# Run every case in order. Each reproduce.sh resets the platform
# first, so this is safe against state accumulated by prior cases.
reproduce-all: reproduce-01 reproduce-02 reproduce-03 reproduce-04 reproduce-05 reproduce-06 reproduce-07

# pytest: runs both the reproduction harness (test_reproductions.py
# loops over every case's reproduce.sh) and the per-case pinning
# tests under tests/test_*.py. Requires the test deps installed
# (`pip install -r tests/requirements.txt`); the live stack must
# also be up for the integration-shaped tests.
test:
	pytest -q

# ruff check on the api code and the test suite. Excludes the
# generated venv and cache dirs by default.
lint:
	ruff check api tests

# Tear down + remove __pycache__ dirs. Doesn't touch .venv,
# .pytest_cache, .ruff_cache (covered by .gitignore but kept around
# for fast re-runs).
clean: down
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
