"""
Tiny TLS HTTP server used by case 07's reproduction (cases/07-tls-
incomplete-chain/reproduce.sh) and as the system under test for the
pinning tests in tests/test_cert_chain.py.

Why this file exists
--------------------
Case 07 is about a server presenting an incomplete certificate chain to
TLS clients — the canonical "leaf-only, intermediate missing" failure
mode that ships every time a CA-vendor switch lands without updating
the server's chain bundle. The lab's main api service serves plain HTTP
(case 04 etc. don't need TLS), so case 07 needs its own TLS-serving
process. This script is that process: a stand-in for nginx / apache /
whatever the customer is actually running. It loads a server cert + key,
wraps a stdlib HTTPServer in an SSL context, and serves until killed.

Why a custom Python script instead of `openssl s_server`
--------------------------------------------------------
Earlier iterations of this case used `openssl s_server` for the same
purpose. It turned out that `openssl s_server`'s chain-presenting
behavior is fragile across versions: depending on which combination of
`-cert`, `-cert_chain`, `-CAfile`, `-build_chain`, and `-chainCAfile`
flags you use, OpenSSL 3.x might still send only the leaf even when you
explicitly hand it the intermediate. Python's `ssl.SSLContext.load_cert_chain`
is well-documented: it sends every certificate in the supplied file
(leaf + intermediates), in order. That predictability is what the case
needs to demonstrate the bug shape reliably.

Boot-time chain-completeness gate
---------------------------------
The case 07 escalation proposes a deploy-pipeline check that refuses to
ship a cert file with fewer than 2 certs (leaf + at least one
intermediate) — because a leaf-only file is the file shape that ships
the original bug. That check is enforced *here*, at process start, so a
config that would ship a broken chain fails fast and visibly with
exit code 78 (EX_CONFIG from sysexits.h, the conventional "config error"
exit). The case's reproduction script intentionally bypasses the gate
with `--allow-leaf-only` to exhibit the bug shape; that flag is also
exercised by tests/test_cert_chain.py to pin the bypass behavior — if
the flag handling is dropped in a refactor, case 07's reproduce.sh
starts failing.

Usage
-----
    python3 tls_server.py <cert-file> <key-file> <port> [--allow-leaf-only]

The first three positional args are required and order-sensitive.
--allow-leaf-only is a defaulted-off flag; pass it only when the
caller's intent is to demonstrate a broken chain (i.e. case 07's
reproduce.sh and the corresponding bypass-flag pinning test).
"""
import http.server
import re
import ssl
import sys


# Conventional exit code for a config error. See `man 3 sysexits` /
# /usr/include/sysexits.h. Using a named constant rather than the
# literal 78 makes the test's `returncode == CHAIN_INCOMPLETE_EXIT`
# assertion self-documenting.
CHAIN_INCOMPLETE_EXIT = 78


def count_pem_certs(path: str) -> int:
    """Return the number of PEM-encoded CERTIFICATE blocks in a file.

    The check looks specifically for the `-----BEGIN CERTIFICATE-----`
    marker, not just any PEM block. A cert file in the wild can contain
    private keys (`-----BEGIN PRIVATE KEY-----` etc.) and other PEM
    objects alongside certificates; counting only CERTIFICATE markers
    gives the number that actually matters for chain completeness.

    Tolerates whitespace and line-ending variation (`re.findall` works
    on the raw text without parsing).
    """
    text = open(path).read()
    return len(re.findall(r"-----BEGIN CERTIFICATE-----", text))


def main() -> None:
    """Parse args, run the chain-completeness gate, start the TLS server.

    The arg parser is hand-rolled because argparse is overkill for three
    positional args and one boolean flag. Order-tolerance for the flag
    (it can appear anywhere in the arg list) is the one nicety; the
    rest of the args are strictly positional.
    """
    args = sys.argv[1:]

    # Allow the bypass flag in any position; remove it from the list
    # before positional-arg validation so the user can write either
    # `tls_server.py cert key port --allow-leaf-only` or
    # `tls_server.py --allow-leaf-only cert key port`.
    allow_leaf_only = "--allow-leaf-only" in args
    if allow_leaf_only:
        args.remove("--allow-leaf-only")

    if len(args) != 3:
        # Exit 2 = usage error (sysexits EX_USAGE). Distinguishes
        # "you called this wrong" from "the cert is broken" (exit 78).
        print(
            "usage: python3 tls_server.py <cert-file> <key-file> <port> "
            "[--allow-leaf-only]",
            file=sys.stderr,
        )
        sys.exit(2)
    cert, key, port_s = args
    port = int(port_s)

    # ---------- chain-completeness gate ----------
    #
    # A 2+ count means leaf + at least one intermediate, which is the
    # minimum a public-CA-signed cert needs to validate against a client
    # whose trust store only has the root. Self-signed certs (count == 1
    # where the leaf is also the root) are a different case — those
    # would fail this check too, but the right answer there is to use
    # `--allow-leaf-only` and document that the deploy is using a
    # non-public-CA chain. The check is intentionally strict and
    # bypassable rather than smart.
    n_certs = count_pem_certs(cert)
    if n_certs < 2 and not allow_leaf_only:
        # Stderr (not stdout) because exit-on-error output should not be
        # captured by callers that pipe stdout to a log file. The error
        # message names the specific count and the bypass flag so an
        # operator who hits this can decide what to do without reading
        # the source.
        print(
            f"chain incomplete: {n_certs} cert in {cert}; "
            f"expected leaf + at least one intermediate. "
            f"Pass --allow-leaf-only to ship the broken chain anyway "
            f"(case 07 reproduction does this on purpose).",
            file=sys.stderr,
        )
        sys.exit(CHAIN_INCOMPLETE_EXIT)

    # ---------- TLS server setup ----------

    # SSLContext per-process. Server-side context (PROTOCOL_TLS_SERVER)
    # because we're terminating TLS, not initiating it.
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    # load_cert_chain is the well-documented stdlib API for "here's the
    # cert (which may include intermediates concatenated after the leaf)
    # and the matching private key". Whatever's in `cert` after the
    # first PEM block is sent to the client as part of the chain.
    ctx.load_cert_chain(certfile=cert, keyfile=key)
    # Pin the minimum protocol version to TLS 1.2 to match the modal
    # nginx default the customer is most likely running. Without this
    # pin, OpenSSL 3.x defaults can negotiate down to TLS 1.0/1.1
    # depending on cipher suite availability, which would be a
    # different (and uninteresting for case 07) failure mode.
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2

    # SimpleHTTPRequestHandler is fine here — case 07 doesn't care what
    # the response body looks like, only that the TLS handshake either
    # validates or doesn't. The handler defaults to serving the cwd's
    # files; we never test that, so the default is harmless.
    server = http.server.HTTPServer(
        ("127.0.0.1", port),
        http.server.SimpleHTTPRequestHandler,
    )
    # Wrap the listening socket in TLS. From here on every accepted
    # connection goes through the TLS handshake before any HTTP bytes
    # are read.
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    # serve_forever blocks; the caller (reproduce.sh, tests) is
    # responsible for sending SIGTERM to stop. trap-handlers in the
    # caller scripts handle the cleanup.
    server.serve_forever()


if __name__ == "__main__":
    main()
