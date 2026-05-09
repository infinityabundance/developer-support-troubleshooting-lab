"""Pinning tests for case 07 — TLS chain completeness gate in
tls_server.py and the from-the-wire path it protects. Self-contained:
each test generates its own throwaway PEM chain in a tempdir."""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TLS_SERVER = REPO_ROOT / "cases" / "07-tls-incomplete-chain" / "tls_server.py"

# Import count_pem_certs directly from the script for unit-shaped tests.
sys.path.insert(0, str(TLS_SERVER.parent))
from tls_server import count_pem_certs, CHAIN_INCOMPLETE_EXIT  # noqa: E402


def _make_chain(workdir: Path) -> tuple[Path, Path, Path, Path]:
    """Generate root CA, intermediate, leaf(CN=localhost), and a fullchain
    file (leaf || intermediate). Returns (root_pem, leaf_pem, fullchain_pem, leaf_key)."""

    def run(*args: str) -> None:
        subprocess.run(args, check=True, cwd=workdir,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-keyout", "root.key", "-out", "root.pem", "-subj", "/CN=Pinning Root CA")
    run("openssl", "req", "-newkey", "rsa:2048", "-nodes",
        "-keyout", "int.key", "-out", "int.csr", "-subj", "/CN=Pinning Intermediate CA")

    ext1 = workdir / "ext1.cnf"
    ext1.write_text("basicConstraints=CA:TRUE\nkeyUsage=keyCertSign\n")
    run("openssl", "x509", "-req", "-in", "int.csr",
        "-CA", "root.pem", "-CAkey", "root.key", "-CAcreateserial",
        "-days", "1", "-out", "int.pem", "-extfile", str(ext1))

    run("openssl", "req", "-newkey", "rsa:2048", "-nodes",
        "-keyout", "leaf.key", "-out", "leaf.csr", "-subj", "/CN=localhost")

    ext2 = workdir / "ext2.cnf"
    ext2.write_text("subjectAltName=DNS:localhost,IP:127.0.0.1\n")
    run("openssl", "x509", "-req", "-in", "leaf.csr",
        "-CA", "int.pem", "-CAkey", "int.key", "-CAcreateserial",
        "-days", "1", "-out", "leaf.pem", "-extfile", str(ext2))

    fullchain = workdir / "fullchain.pem"
    fullchain.write_bytes((workdir / "leaf.pem").read_bytes() +
                          (workdir / "int.pem").read_bytes())

    return (workdir / "root.pem", workdir / "leaf.pem",
            fullchain, workdir / "leaf.key")


@pytest.fixture
def chain(tmp_path):
    return _make_chain(tmp_path)


def test_count_pem_certs_counts_correctly(chain):
    _, leaf, fullchain, _ = chain
    assert count_pem_certs(str(leaf)) == 1
    assert count_pem_certs(str(fullchain)) == 2


def test_server_refuses_to_start_with_leaf_only(chain):
    """The boot-time gate: leaf-only cert must produce a non-zero exit
    with a clear chain-incomplete message and the documented exit code.
    A regression here ships the original bug shape without warning."""
    _, leaf, _, leaf_key = chain
    proc = subprocess.run(
        [sys.executable, str(TLS_SERVER), str(leaf), str(leaf_key), "0"],
        capture_output=True, text=True, timeout=5,
    )
    assert proc.returncode == CHAIN_INCOMPLETE_EXIT, (
        f"expected exit {CHAIN_INCOMPLETE_EXIT} (EX_CONFIG); got {proc.returncode}.\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )
    assert "chain incomplete" in proc.stderr.lower()
    assert "1 cert" in proc.stderr  # The exact count is in the error.


def test_server_starts_with_fullchain(chain, unused_tcp_port):
    """The fixed config must boot cleanly. We don't actually serve
    requests here — start, check it's listening, kill. If the chain-
    completeness check ever rejects a 2+ cert file, this catches it."""
    _, _, fullchain, leaf_key = chain
    proc = subprocess.Popen(
        [sys.executable, str(TLS_SERVER), str(fullchain), str(leaf_key), str(unused_tcp_port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        listening = False
        for _ in range(30):
            try:
                with socket.create_connection(("127.0.0.1", unused_tcp_port), timeout=0.2):
                    listening = True
                    break
            except OSError:
                time.sleep(0.1)
        assert listening, "tls_server.py did not start listening with a valid fullchain"
        # Belt-and-braces: process must still be alive.
        assert proc.poll() is None, f"process exited early with {proc.returncode}"
    finally:
        proc.terminate()
        proc.wait(timeout=3)


def _wait_for_listener(port: int, attempts: int = 30, interval: float = 0.1) -> bool:
    for _ in range(attempts):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except OSError:
            time.sleep(interval)
    return False


def test_endpoint_validates_from_cold_trust_store(chain, unused_tcp_port):
    """Cold curl (--cacert root.pem --no-sessionid): fullchain server →
    200, leaf-only server → exit 60. The boot-time gate catches missing
    intermediates; this catches a wrong intermediate that the gate would
    let through."""
    root_pem, leaf_pem, fullchain_pem, leaf_key = chain

    # POSITIVE: full chain + cold curl session against root only -> 200.
    proc = subprocess.Popen(
        [sys.executable, str(TLS_SERVER), str(fullchain_pem), str(leaf_key), str(unused_tcp_port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        assert _wait_for_listener(unused_tcp_port), "fullchain server did not start"
        result = subprocess.run(
            ["curl", "-sS", "--cacert", str(root_pem), "--no-sessionid",
             "-o", "/dev/null", "-w", "%{http_code}",
             f"https://localhost:{unused_tcp_port}/"],
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 0, (
            f"curl against fullchain endpoint with cold trust store failed: "
            f"rc={result.returncode}, stderr={result.stderr!r}. "
            f"The server is presenting a chain that doesn't validate against "
            f"the configured root."
        )
        assert result.stdout.strip() == "200", (
            f"expected HTTP 200 from fullchain endpoint; got {result.stdout!r}"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=3)

    # Allow the OS to release the port before reusing it.
    for _ in range(30):
        try:
            s = socket.create_connection(("127.0.0.1", unused_tcp_port), timeout=0.1)
            s.close()
            time.sleep(0.1)
        except OSError:
            break

    # NEGATIVE: leaf-only (via bypass) + same cold curl -> exit 60.
    proc = subprocess.Popen(
        [sys.executable, str(TLS_SERVER), str(leaf_pem), str(leaf_key),
         str(unused_tcp_port), "--allow-leaf-only"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        assert _wait_for_listener(unused_tcp_port), "leaf-only server did not start"
        result = subprocess.run(
            ["curl", "-sS", "--cacert", str(root_pem), "--no-sessionid",
             "-o", "/dev/null", "-w", "%{http_code}",
             f"https://localhost:{unused_tcp_port}/"],
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 60, (
            f"expected curl exit 60 (unable to get local issuer certificate) "
            f"against leaf-only endpoint with cold trust store; got "
            f"rc={result.returncode}, stderr={result.stderr!r}"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=3)


def test_server_starts_with_leaf_only_when_bypass_flag_passed(chain, unused_tcp_port):
    """The reproduction's escape hatch: --allow-leaf-only bypasses the
    gate so case 07 can demonstrate the original bug. If the flag stops
    being honored, case 07's reproduce.sh starts failing — this catches
    that before the case does."""
    _, leaf, _, leaf_key = chain
    proc = subprocess.Popen(
        [sys.executable, str(TLS_SERVER), str(leaf), str(leaf_key),
         str(unused_tcp_port), "--allow-leaf-only"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        listening = False
        for _ in range(30):
            try:
                with socket.create_connection(("127.0.0.1", unused_tcp_port), timeout=0.2):
                    listening = True
                    break
            except OSError:
                time.sleep(0.1)
        assert listening, "tls_server.py did not start with --allow-leaf-only"
    finally:
        proc.terminate()
        proc.wait(timeout=3)


@pytest.fixture
def unused_tcp_port():
    """Allocate a free TCP port from the OS, then close the socket so
    the test can rebind. Standard pytest pattern; avoids the
    pytest-asyncio dependency."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
