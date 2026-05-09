# Case 07 — TLS handshake fails: server presents leaf only, intermediate missing

**Ticket #4711, P1.** Customer reports: *"Half of our clients started getting `unable to get local issuer certificate` from your endpoint this week. The other half work fine. Nothing changed on your end?"*

The "half of clients" framing is the part to believe and the part to interrogate. Most "intermittent TLS failures" are actually deterministic-but-client-dependent: the clients that have the missing intermediate cached from some other prior negotiation succeed; the cold ones fail. Same server, two outcomes, decided entirely by what's already in each client's trust chain cache.

## What `openssl s_client` actually shows

The diagnostic is one command and reading three lines of its output. From the customer's environment, with their CA bundle:

```
$ echo | openssl s_client -connect api.example.com:443 -showcerts -CAfile /etc/ssl/certs/ca-bundle.crt
```

What we want to read is two things in the response. First, the chain of certs the **server presented** (the `Certificate chain` block). Second, the verify result (`Verify return code:` near the bottom). Run the case's reproduction and `cat logs.txt` for the side-by-side. The two halves of the file are this same command, before and after the fix:

```
=== BEFORE FIX: server presents leaf only (cert=leaf.pem) ===
depth=0 CN=localhost
verify error:num=20:unable to get local issuer certificate
verify error:num=21:unable to verify the first certificate
Certificate chain
 0 s:CN=localhost
   i:CN=Test Intermediate CA
Verify return code: 21 (unable to verify the first certificate)
```

Read that bottom-up. `Verify return code: 21` is curl's exit-60 in another wrapper. `Certificate chain` lists *one* cert: the leaf, with subject `CN=localhost` and issuer `CN=Test Intermediate CA`. The client knows the root CA. The client does **not** know the intermediate. The server didn't send the intermediate. So the client cannot bridge `localhost` (signed by intermediate, which it doesn't have) up to root (which it does). Verify fails at depth 0 — *the first certificate it sees* — because that one cert is unauthenticated by anything in the trust store.

The "half of clients work" symptom is now obvious: clients that have the intermediate from a prior unrelated TLS session can complete the chain themselves; clients booting fresh can't. The fix is on the server, not the clients.

## What it looks like fixed

```
=== AFTER FIX: server presents full chain (cert=fullchain.pem) ===
depth=2 CN=Test Root CA
depth=1 CN=Test Intermediate CA
depth=0 CN=localhost
Certificate chain
 0 s:CN=localhost
   i:CN=Test Intermediate CA
 1 s:CN=Test Intermediate CA
   i:CN=Test Root CA
Verify return code: 0 (ok)
```

Two certs in the chain. Verify climbs to depth 2 (the root), finds it in the trust store, and validates downward. `Verify return code: 0`.

The change between the two runs is one line in the server config: load the cert *file* that contains leaf followed by intermediate, instead of leaf alone. Nothing about the leaf, the keys, or the trust store changes. The server's job is to send enough chain that the client can walk it; "enough" means everything between the leaf and the root the client already trusts.

## Reproduction

```bash
./reproduce.sh
```

Generates a real 3-tier chain (root CA → intermediate → leaf, all dated today, valid for 24h) in a tempdir, runs a tiny Python TLS server twice — once configured to present the leaf alone, once configured to present leaf+intermediate — and curls each. Records the curl exit codes and the `openssl s_client` chain output to `logs.txt`. Self-contained: no docker-compose stack required, since TLS support tickets are about cert files and trust stores, not the application platform.

## Root cause

The server cert file on disk has only the leaf in it. nginx, apache, and most TLS terminators behave the same way: they send exactly what's in the file. If the cert file is `leaf.pem`, the chain to the client is one cert; if it's `cat leaf.pem intermediate.pem > fullchain.pem`, the chain is two. There is no chain "discovery" on the server side — the operator has to put the intermediate in the file. The customer's recent CA-vendor change (Let's Encrypt → DigiCert, internal CA → public, etc.) is the kind of change that flips a previously-working chain into a leaf-only one because the chain-bundling step in the issuance script is per-vendor and didn't carry over.

## Fix

**Workaround (server-side, today):** concatenate the intermediate(s) into the cert file the server loads. For nginx: `ssl_certificate /etc/ssl/api/fullchain.pem;` where `fullchain.pem` is `cat leaf.pem intermediate.pem`. Reload nginx. New TLS sessions get the full chain immediately; existing ones aren't affected (they already negotiated). For apache: same idea, `SSLCertificateFile` should point at the concatenated file in modern apache (the older `SSLCertificateChainFile` directive is deprecated but still works on 2.4.8+).

**Workaround (client-side, only if you do not control the server):** install the intermediate into the client's trust store. Slow, error-prone, and leaks ops work to every customer. Don't recommend.

**Proper fix:** make the issuance pipeline emit a `fullchain.pem` artifact and make the server config reference *that*, not the leaf-only cert. ACME clients (certbot, lego) emit `fullchain.pem` by default; the trap is config that points at `cert.pem` (leaf) instead. Add a check in the deploy pipeline: parse the cert file the server is about to load, count the certs, fail the deploy if it's 1.

## Pinning test that prevents recurrence

Two pinning tests in `tests/test_cert_chain.py`:

- `test_server_refuses_to_start_with_leaf_only` — pins the boot-time gate. The TLS server reads its cert file, counts cert blocks, refuses to start with a non-zero exit and a clear chain-incomplete message if the count is <2. A regression that drops the gate fails this test immediately.
- `test_endpoint_validates_from_cold_trust_store` — pins the from-the-wire path the gate is meant to protect. Spins up the TLS server with a full leaf+intermediate chain, runs `curl --cacert <only the configured root> --no-sessionid` against it from a clean session with no cached intermediates — what an actual cold client looks like — and asserts a 200. The companion negative case asserts curl exit 60 against a leaf-only-served endpoint. If the deploy regresses to leaf-only, this fails before any client traffic touches it.

## Adjacent failure modes (not hit in this case, but the same pattern)

- **Wrong-order chain.** File contains intermediate before leaf instead of leaf before intermediate. Some clients tolerate this; some don't (Java's TLS stack is famously strict). Symptom: works from curl, fails from a JVM. Diagnostic: `openssl s_client -showcerts` shows the chain in the wrong order; the leaf must come first.
- **Expired intermediate while leaf is still valid.** Vendor rotated their intermediate; old intermediate expired last week; your server is still presenting the old one alongside a new leaf signed by a new intermediate. Symptom: clients that pin to the old intermediate accept it (because trust); clients doing fresh path-building reject it (because the intermediate is past `notAfter`). Diagnostic: `openssl x509 -in intermediate.pem -noout -dates` against the actual file the server loaded. Fix: refresh the intermediate from the vendor's current chain bundle.
- **Cross-signed root not yet trusted.** The CA recently switched their roots, and your client's trust store still has the old root. Server presents new leaf + new intermediate signed by new root; client rejects "unknown issuer." Same error message as this case from the customer's logs. Diagnostic: `openssl s_client -showcerts` on the server, then check whether the *root* the chain points to is in the client's `/etc/ssl/certs`. Fix: either pin to a cross-signed intermediate that bridges old and new roots, or update client trust stores. This is the painful version of the case to ship through customer fleets.

The unifying rule: the server sends what's in the cert file; the client validates against what's in the trust store; "incomplete chain" means the file on the server doesn't bridge to the client's roots. Two different stores, one TLS handshake.
