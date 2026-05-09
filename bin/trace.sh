#!/usr/bin/env bash
# Trace a single request across api + db logs, ordered by timestamp.
#
# Usage: bin/trace.sh <request-id>
#   or:  make trace REQUEST=<request-id>
#
# Why this exists
# ---------------
# Distributed tracing (Datadog APM, Honeycomb, OpenTelemetry spans) is
# the production-grade tool for "what was the system doing during this
# request". The lab doesn't run any of those — it has docker compose
# logs and grep. This script wraps that cheap stack into something that
# behaves like a single-request trace: give it an rid, get back the api
# and db log lines that overlap that request's lifetime, ordered by
# wall-clock time.
#
# How it works
# ------------
# The api request_id_and_timing middleware (api/main.py) tags every api
# log line with `rid=<hex12>`. db logs aren't tagged with rid because
# Postgres has no awareness of our request lifecycle; they have their
# own timestamps and that's all this script can correlate against. So:
#
#   1. Grep the api log stream for the rid; pull those lines.
#   2. Compute the timestamp window covering the matched api lines (±1s
#      to absorb network jitter and middleware overhead at both ends).
#   3. Pull every db log line whose timestamp falls inside that window.
#   4. Tag each line with its service name, sort by leading timestamp,
#      print.
#
# The result is the api lines for this request interleaved with whatever
# the db was doing while the request was in flight. Short of full
# distributed tracing, but enough to make case-04-shaped bugs (HMAC over
# re-serialized bytes) jump off the screen because the body_len field,
# the db round-trips, and the response code all line up in time order.
# See cases/04-webhook-hmac-reserialization/README.md for a worked example.
#
# Log-format contract this script assumes
# ---------------------------------------
# This is the load-bearing assumption: if it changes, the tracer
# silently produces garbage. The contract is pinned by tests/test_trace.py,
# and the doctrine of "every diagnostic tool assumes a log format; name
# it explicitly" is in TRIAGE.md step 7.
#
#   - `$COMPOSE logs --no-color --timestamps --no-log-prefix <svc>`
#     emits one line per log entry.
#   - The leading whitespace-separated token on each line is an
#     ISO-8601 timestamp parseable by GNU `date -d` — any subsecond
#     precision (microseconds, nanoseconds, none), any timezone shape
#     (`Z`, `+HH:MM`, `-HHMM`).
#   - The rest of the line is the log message; api lines tagged with
#     `rid=<hex>` are the ones this script greps for.
#
# Failure modes if the contract breaks:
#   - Log driver reconfigured to emit JSON: every `date -d` call fails
#     silently, db_lines is empty, api lines may still appear (greppable
#     for rid as a substring in JSON), output is misleading.
#   - Service starts prefixing each line with extra fields: $1 of awk
#     no longer points at the timestamp; date -d fails.
#   - --no-log-prefix flag removed from a future docker compose: the
#     "service-N |" prefix returns and $1 is the service name. tracer
#     produces empty output (because no $1 parses as a timestamp).

set -euo pipefail

# ---------- argument validation ----------

if [[ $# -lt 1 ]]; then
    # Exit 2 (not 1) follows the convention "1 = task failed, 2 = invocation
    # was wrong". Lets a caller distinguish "rid not found" from "you didn't
    # pass an rid".
    echo "usage: $0 <request-id>" >&2
    exit 2
fi

RID="$1"

# COMPOSE indirection: tests/test_trace.py overrides this to point at a
# fake-compose script that emits canned log output for unit testing
# without needing the docker-compose stack to be up. Production callers
# get the default `docker compose`.
COMPOSE="${COMPOSE:-docker compose}"

# --no-color keeps the output greppable (no ANSI escape sequences).
# --timestamps prefixes each line with an ISO-8601 timestamp.
# --no-log-prefix strips the "service-N |" service-name padding so
# awk's $1 is the timestamp, not the service prefix.
LOGFLAGS="--no-color --timestamps --no-log-prefix"

# ---------- step 1: api lines matching the rid ----------

# `|| true` to keep `set -e` from killing the script when grep finds no
# matches — we want to handle the no-match case below with a clear error
# message, not a cryptic "command failed" exit.
api_lines="$($COMPOSE logs $LOGFLAGS api 2>/dev/null | grep -F "rid=$RID" || true)"

if [[ -z "$api_lines" ]]; then
    # Most common cause is a typo in the rid; second most common is the
    # request being old enough that docker compose has rotated its log
    # buffer. The error message names both so the caller can self-diagnose.
    echo "no api log lines matching rid=$RID — is the rid correct, and was the request recent enough to still be in container logs?" >&2
    exit 1
fi

# ---------- step 2: timestamp window covering the api lines ----------

# First/last timestamps in the api-lines block. The block is already in
# emission order (docker compose logs preserves order), so head/tail of
# the block give the window endpoints directly. awk '{print $1}' pulls
# the leading ISO-8601 token.
first_ts="$(echo "$api_lines" | head -n1 | awk '{print $1}')"
last_ts="$(echo "$api_lines" | tail -n1 | awk '{print $1}')"

# Convert to epoch seconds. `date -d` handles the full ISO-8601 shape
# range (subsecond precision and timezone offset are both tolerated).
first_epoch="$(date -d "$first_ts" +%s)"
last_epoch="$(date -d "$last_ts" +%s)"

# ±1s margin absorbs middleware overhead (the api log line is emitted
# slightly after the actual db query happens) and network jitter inside
# the docker network. Wider margins risk pulling in unrelated db lines;
# tighter margins risk missing db work that legitimately belongs to
# this request. 1s is the empirically-right value for the lab; tune up
# for slower environments.
window_start="$(date -u -d "@$((first_epoch - 1))" +%Y-%m-%dT%H:%M:%SZ)"
window_end="$(date -u -d "@$((last_epoch + 1))" +%Y-%m-%dT%H:%M:%SZ)"

# ---------- step 3: db lines whose timestamp is in the window ----------

# Compare in epoch seconds, not as ISO-8601 strings. Two streams whose
# wall-clock seconds match but whose offset shapes differ ("Z" vs
# "+01:00") would not align under string compare; epoch normalizes both
# to the same scalar. This is the silent-failure mode that bites the
# moment a log driver gets reconfigured to emit a different timezone
# shape — pinned by tests/test_trace.py::test_handles_mixed_timezone_suffix_between_streams.
window_start_epoch="$((first_epoch - 1))"
window_end_epoch="$((last_epoch + 1))"

db_lines="$($COMPOSE logs $LOGFLAGS db 2>/dev/null \
    | while IFS= read -r line; do
        # ${line%% *} = drop everything from the first space onward.
        # Cheaper than awk for this single-token extraction.
        ts="${line%% *}"
        # `date -d` parses ISO-8601 with or without subseconds and any
        # timezone shape. Lines whose first token isn't parseable
        # (blank lines, headers, multi-line continuations) get dropped
        # silently — appropriate for log filtering where occasional
        # garbage is expected.
        if epoch="$(date -d "$ts" +%s 2>/dev/null)"; then
            if [[ "$epoch" -ge "$window_start_epoch" && "$epoch" -le "$window_end_epoch" ]]; then
                printf '%s\n' "$line"
            fi
        fi
    done)"

# ---------- step 4: tag, merge, time-order, print ----------

# Tag each line with its source service so the merged output is readable
# (without the tag, you can't tell at a glance whether a given line came
# from api or db). `[api] ` and `[db]  ` (with extra space on `db`) keep
# the column widths aligned for visual scanning.
#
# `sort -b -k2,2` orders by the SECOND whitespace-separated field,
# which is the timestamp after the [api]/[db] prefix. `-b` ignores the
# extra alignment blank in `[db]  `; without it, GNU sort treats that
# blank as part of the key and can place db lines before earlier api
# lines.
{
    echo "$api_lines" | sed 's/^/[api] /'
    # `if/then` rather than `&&` because the script runs under `set -e`:
    # the && short-circuit returns 1 when db_lines is empty, which would
    # propagate through the surrounding pipeline and exit the script
    # nonzero even though the trace itself succeeded. The if/then form
    # short-circuits cleanly with exit 0.
    if [[ -n "$db_lines" ]]; then
        echo "$db_lines" | sed 's/^/[db]  /'
    fi
} | sort -b -k2,2
