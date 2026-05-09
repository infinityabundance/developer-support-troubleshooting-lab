# Escalation: TLS server presenting leaf-only chain after CA-vendor switch

**Severity:** P1 (production traffic failing for cold-cache clients; workaround on our side)
**Component:** TLS termination layer (nginx); cert-issuance pipeline
**Triggering ticket:** #4711

## What happened

We rotated our public CA last sprint. The new vendor's issuance script emits the leaf cert and the intermediate as separate files. Our nginx config references the leaf file directly. The intermediate is on disk; it just isn't in the file the server loads. Nginx sends what's in the loaded cert file, which is one cert. Clients without the intermediate cached fail verify at depth 0 with errno 20 / errno 21.

Symptoms looked intermittent because clients with the intermediate already cached from a different TLS session validated successfully — the chain-completion happened in their TLS stack, not on the wire. Cold clients (CI runners spinning up fresh containers, customers with strict trust stores, anything that doesn't aggressively cache intermediates) failed deterministically.

## Why this got past review

A symmetry-trap instance (see *Why configuration drift escapes tests* in `docs/support-casebook.md`). The deploy that rotated the CA tested by curl-ing the endpoint from the staging operator's laptop. That laptop already had the new intermediate cached from an unrelated browser session earlier in the week. The TLS verify succeeded. We took that as evidence that the chain was complete and shipped.

The test answered "can my laptop reach the endpoint?" The question that mattered in production was "can a fresh client with only the configured root CAs reach the endpoint?" Those are different questions. We had no test that ran from a known-cold trust environment — the operator's laptop and the production-shipped chain shared a side channel (the browser's intermediate cache) that production clients did not.

## Why this will recur

CA rotations happen once or twice a year. Issuance scripts vary by vendor (Let's Encrypt's `fullchain.pem` is leaf-then-intermediates; DigiCert ships a separate `DigiCertCA.crt`; internal CAs vary by tooling). Every rotation is an opportunity to lose the intermediate from the loaded file. The current mitigation is "the operator runs `openssl s_client` and counts certs"; the failure mode is silent because the deploy pipeline doesn't enforce it.

## Proposed fix (two changes)

1. **Issuance pipeline emits one canonical artefact.** Whatever the vendor returns, the pipeline normalizes it to `fullchain.pem` (leaf || intermediate(s) || optional cross-sign), validates it with `openssl verify` against the configured root bundle, and refuses to publish the artefact if validation fails. This is ~50 lines of bash plus the validation step.

2. **Boot-time chain check in the server.** Before the TLS terminator binds the listening socket, parse the cert file it's about to load, count the cert blocks, refuse to start if it's <2 (assuming we're behind a public CA — internal-only services are configured separately). The check uses `openssl crl2pkcs7 -nocrl -certfile <file> | openssl pkcs7 -print_certs -noout` to count subjects. ~20 lines of bash; runs as part of the systemd unit's `ExecStartPre`.

The combination means a CA rotation that drops the intermediate fails at the pipeline first, fails at server-boot if it gets through, and never reaches a client.

## Pinning test that prevents recurrence

`tests/test_cert_chain.py::test_endpoint_validates_from_cold_trust_store` — runs `curl --cacert <only the configured root> --no-sessionid https://endpoint/` against the staging endpoint inside a clean container with no `~/.ssl` state. Asserts the curl returns 0 and the reported chain length is ≥2. Crucially, it runs from an environment that has *only* the configured root in its trust store and no cached intermediates — which is what an actual cold client looks like. If the deploy regresses to leaf-only, this test fails before traffic touches it.

## What this does not solve

A vendor that issues a leaf signed by an intermediate that *itself* isn't yet widely cross-signed (early-rotation period) can still cause problems for clients on stricter trust policies even after we ship the full chain. That's a vendor-engagement issue, not a server-config issue. Out of scope here; track separately.

A client that ignores `Verify return code` and accepts unauthenticated certs (curl with `-k`, applications with verify disabled) will appear to succeed against the broken server, masking the issue from their own monitoring. We can't detect this from our side; it's a customer-side configuration question worth a separate doc page.
