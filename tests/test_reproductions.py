"""
The reproduction harness. For every directory under cases/, runs the
reproduce.sh inside it and asserts that the normalized stdout contains
each line of the case's expected-output.txt, in order.

If a case stops reproducing the way it's supposed to (the bug got
"fixed" without updating the expected-output, or a refactor moved the
broken endpoint, or the script's output format changed), this test
fails and CI blocks the merge. That's the contract: the bug stays
visible until someone explicitly updates the expected-output.

Requires the live docker-compose stack (`make up` before `pytest`).
The case-07 reproduction is self-contained and doesn't need the stack;
all the others do.
"""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = ROOT / "cases"

# Auto-discover cases by scanning cases/ for subdirectories. Adding a
# new case is just `mkdir cases/NN-slug && touch reproduce.sh
# expected-output.txt` — pytest picks it up on the next run with no
# harness change. sorted() so the parametrize order is deterministic
# across runs (matters for CI output readability, not correctness).
CASES = sorted(p.name for p in CASES_DIR.iterdir() if p.is_dir())


def _normalize(s: str) -> str:
    """Strip per-run noise so the diff is stable across runs and machines.

    Four substitutions:
      - rid=<hex>: every reproduce.sh's request id is fresh per run;
        normalized to a placeholder so the expected-output doesn't
        need to (and can't) include the literal id.
      - ISO-8601 timestamps: same, fresh per run.
      - dur_ms=<float>: hardware-dependent; the case 05 README explicitly
        argues we should NOT pin durations.
      - 10-digit decimals: catches Unix epoch timestamps embedded in
        webhook signatures (case 04). Without this, the X-Timestamp
        value would leak into the diff.
    """
    s = re.sub(r"rid=[0-9a-f]+", "rid=<RID>", s)
    s = re.sub(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\b", "<TS>", s)
    s = re.sub(r"dur_ms=[0-9.]+", "dur_ms=<N>", s)
    s = re.sub(r"\b\d{10}\b", "<EPOCH>", s)
    return s.strip()


@pytest.mark.parametrize("case", CASES)
def test_case_reproduces(case: str):
    """Run cases/<case>/reproduce.sh, verify its stdout matches
    cases/<case>/expected-output.txt under in-order substring (or
    regex:) matching.

    120s timeout per case. Case 03 and 06 are the ones most likely to
    bump up against this — case 03's bind-restart waits up to 10s for
    the api to come up on the new bind; case 06's apk-install is the
    longest first-time-run step. The remaining cases finish in well
    under 5s.
    """
    case_dir = CASES_DIR / case
    script = case_dir / "reproduce.sh"
    expected_file = case_dir / "expected-output.txt"

    # Belt-and-braces validation: every case directory MUST have these
    # two files. A new case missing either fails this assertion with a
    # clear message rather than producing a confusing test outcome
    # later.
    assert script.exists(), f"missing reproduce.sh for {case}"
    assert expected_file.exists(), f"missing expected-output.txt for {case}"

    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True, text=True, timeout=120,
        cwd=ROOT,
    )

    got = _normalize(result.stdout)
    expected = _normalize(expected_file.read_text())

    # Each expected line must appear (in order) somewhere in the actual stdout.
    # Default per-line match is substring (reproduce.sh prints diagnostic
    # lines around the contract output). A line prefixed `regex:` is matched
    # as a regex instead — used where substring would be too loose to guard
    # the contract (e.g. queries=201 vs queries=2010, where substring would
    # match "queries=201" inside "queries=2010" silently).
    #
    # `cursor` advances after each match so an expected line cannot match
    # output that appeared before a previous expected line. This catches
    # output-reordering regressions that a set-based check would miss.
    expected_lines = [line for line in expected.splitlines() if line.strip()]
    cursor = 0
    for line in expected_lines:
        if line.startswith("regex:"):
            pattern = line[len("regex:"):]
            m = re.search(pattern, got[cursor:])
            assert m is not None, (
                f"\n[{case}] expected regex did not match remaining stdout:\n"
                f"  pattern: {pattern!r}\n"
                f"  stdout was:\n{result.stdout}\n"
                f"  stderr was:\n{result.stderr}"
            )
            # m.end() is in the substring `got[cursor:]`, so we advance
            # the absolute cursor by the match's end-position relative
            # to where we started searching.
            cursor += m.end()
        else:
            idx = got.find(line, cursor)
            assert idx >= 0, (
                f"\n[{case}] expected line not found in stdout:\n"
                f"  expected: {line!r}\n"
                f"  stdout was:\n{result.stdout}\n"
                f"  stderr was:\n{result.stderr}"
            )
            cursor = idx + len(line)
