# Image for the api service. Built once via docker-compose's build
# context (./api) and run as the `api` service.
#
# Base: python:3.12-slim (Debian-based, glibc). NOT alpine — the lab
# explicitly recommends glibc bases over musl ones (case 06's whole
# point), so the api image practices what the case preaches. The
# alpine-resolver sidecar is the only place we use musl, and it's
# there specifically to demonstrate the bug.

FROM python:3.12-slim

WORKDIR /app

# curl + wget: needed by the docker-compose healthcheck (which uses
# wget) and by reproduce scripts that exec into the container (which
# may use either). --no-install-recommends keeps the image smaller;
# rm -rf the apt lists keeps installation residue out of the image
# layer.
RUN apt-get update && apt-get install -y --no-install-recommends curl wget \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps in a separate layer from the application code so
# changing api/main.py doesn't bust the pip-install cache. This is the
# standard Dockerfile dependency-caching pattern.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code (main.py, log_config.json, this Dockerfile,
# requirements.txt). The Dockerfile and requirements.txt being copied
# again is harmless — Docker's COPY . . is just `cp -r`.
COPY . .

EXPOSE 8000

# uvicorn launch line. Defaults: BIND_HOST=0.0.0.0, BIND_PORT=8000.
# Compose injects BIND_HOST from the shell env (so case 03's
# reproduce.sh can flip it to 127.0.0.1 to demonstrate the bug); the
# `:-0.0.0.0` fallback inside the shell expansion ensures the default
# is always reachable from outside the container.
#
# --log-config points at the JSON-defined logging config that gives
# api log lines the format `bin/trace.sh` greps for. Changing the
# log format here (or in log_config.json) without updating the
# tracer breaks `make trace` silently — the log-format-contract
# trap that TRIAGE.md step 7 names.
CMD ["sh", "-c", "uvicorn main:app --host ${BIND_HOST:-0.0.0.0} --port ${BIND_PORT:-8000} --log-config log_config.json"]
