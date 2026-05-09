"""
Unit tests for bin/trace.sh — log-format contract.

The tracer derives a timestamp window from api log lines matching a
given rid, then includes db log lines whose timestamps fall in that
window. Both sides convert their per-line timestamps to epoch seconds
via `date -d` before comparing, so timezone differences between
streams (one in UTC `Z`, another in `+01:00`) and subsecond-precision
differences both normalize to the same scalar. If that conversion
drifts (e.g. a future log driver emits a non-ISO-8601 format that
date -d can't parse), the tracer silently produces wrong output.

These tests synthesize fake docker-compose log streams in a few
shapes the conversion must handle, run trace.sh against them, and
assert the right db lines come through.

Pure shell harness — no docker-compose stack required. The COMPOSE
env var that trace.sh respects is overridden to point at a fake
script that emits canned log output for `logs <flags> api` and
`logs <flags> db`.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TRACE_SH = REPO_ROOT / "bin" / "trace.sh"


def _fake_compose(tmp_path: Path, api_log: str, db_log: str) -> Path:
    """Write a tiny shell script that mimics `docker compose logs <flags> <service>`
    by printing the api_log when called with `... api` and db_log when called
    with `... db`. Returns the script path; caller passes it as $COMPOSE."""
    api_file = tmp_path / "api.log"
    db_file = tmp_path / "db.log"
    api_file.write_text(api_log)
    db_file.write_text(db_log)

    fake = tmp_path / "fake-compose"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        '# $@ is e.g. "logs --no-color --timestamps --no-log-prefix api"\n'
        f'svc="${{@: -1}}"\n'
        f'case "$svc" in\n'
        f'  api) cat {api_file} ;;\n'
        f'  db)  cat {db_file} ;;\n'
        f'  *)   exit 1 ;;\n'
        f'esac\n'
    )
    fake.chmod(0o755)
    return fake


def _run_trace(tmp_path: Path, rid: str, api_log: str, db_log: str) -> subprocess.CompletedProcess:
    fake = _fake_compose(tmp_path, api_log, db_log)
    env = os.environ.copy()
    env["COMPOSE"] = str(fake)
    return subprocess.run(
        ["bash", str(TRACE_SH), rid],
        env=env, capture_output=True, text=True, timeout=10,
    )


def test_includes_db_lines_with_subsecond_precision_in_window(tmp_path):
    """The original bug: api logs had subsecond precision but the
    comparison string-compared the full ISO timestamp including
    subseconds, so a db line with the same wall-clock second but a
    larger fractional component sorted ABOVE the window's upper bound
    derived from a different stream's truncated stamp. After the fix
    the per-line stamp is normalized to seconds, and lines whose second
    matches the window are included."""
    api = (
        "2026-05-09T01:05:34.443265000+01:00 INFO api rid=trace-x "
        "method=POST path=/webhook status=401\n"
    )
    db = (
        # Inside the window (the api line is at 01:05:34, window is ±1s).
        "2026-05-09T01:05:34.999888000+01:00 LOG: statement: SELECT 1\n"
        # Outside the window (10s later).
        "2026-05-09T01:05:44.000000000+01:00 LOG: statement: SELECT 2\n"
    )
    r = _run_trace(tmp_path, "trace-x", api, db)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "[api]" in out and "rid=trace-x" in out
    assert "SELECT 1" in out
    assert "SELECT 2" not in out


def test_handles_mixed_timezone_suffix_between_streams(tmp_path):
    """A second log driver might emit `Z` (UTC) where another emits
    `+00:00`. The normalization strips the suffix; lexicographic
    compare against the window then works. Without normalization, the
    raw strings would diverge at the suffix position."""
    api = "2026-05-09T01:05:34.000000000+00:00 INFO api rid=trace-y status=200\n"
    db = (
        # Same wall-clock second, different suffix shape.
        "2026-05-09T01:05:34.500000000Z LOG: statement: SELECT inside\n"
        # 5s later, outside the window.
        "2026-05-09T01:05:39.000000000Z LOG: statement: SELECT outside\n"
    )
    r = _run_trace(tmp_path, "trace-y", api, db)
    assert r.returncode == 0, r.stderr
    assert "SELECT inside" in r.stdout
    assert "SELECT outside" not in r.stdout


def test_exits_nonzero_when_no_api_lines_match(tmp_path):
    """A rid the tracer cannot find should produce a clear stderr
    message and a nonzero exit. Otherwise a typo silently looks like
    'no activity for this request,' which is misleading."""
    api = "2026-05-09T01:05:34.000000000Z INFO api rid=other status=200\n"
    db = ""
    r = _run_trace(tmp_path, "trace-missing", api, db)
    assert r.returncode != 0
    assert "no api log lines matching rid=trace-missing" in r.stderr.lower()


def test_lines_are_time_ordered_in_output(tmp_path):
    """Mixed-service lines should print in time order so the diagnostic
    reads top-to-bottom. The api+db combined output is sorted by the
    leading timestamp on each line."""
    api = (
        "2026-05-09T01:05:34.000Z INFO api rid=trace-z step=A\n"
        "2026-05-09T01:05:34.500Z INFO api rid=trace-z step=C\n"
    )
    db = (
        "2026-05-09T01:05:34.250Z LOG: db step=B\n"
    )
    r = _run_trace(tmp_path, "trace-z", api, db)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    # Find the position of each step marker; they should appear in A,B,C order.
    pos_a = out.find("step=A")
    pos_b = out.find("step=B")
    pos_c = out.find("step=C")
    assert pos_a < pos_b < pos_c, f"order broken: A={pos_a} B={pos_b} C={pos_c}\noutput:\n{out}"
