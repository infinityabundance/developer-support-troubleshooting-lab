# Escalation: Alpine + musl + ndots — recurring DNS surprise for customer workers

**Severity:** P3 (recurring; user-recoverable but real)
**Component:** customer-shipped image base; documentation
**Triggering ticket:** #4640

## What happened

Customer's worker runs on Alpine (musl). On a custom Compose network, intermittent NXDOMAIN on the short service name `api`. Root cause is the interaction of musl's resolver with Docker's embedded DNS and the resolv.conf state. FQDN with trailing dot resolves reliably; short name does not.

## Why this is worth escalating

Third Alpine-DNS ticket in two quarters. Same root cause each time. Median diagnostic time ~30 minutes per instance because the symptom presents as intermittent and the cheapest disproof (run the lookup five times) isn't documented anywhere a tier-1 engineer would find it.

## Proposed fix

1. **Documentation:** add an explicit "Alpine and DNS" callout to the deployment guide. Recommend glibc-based images. If Alpine, recommend FQDN-with-trailing-dot for service-name lookups.
2. **Reference image:** switch the customer-shipped sample worker image from `python:3.12-alpine` to `python:3.12-slim`. The size delta is ~30MB; the support delta is hours per ticket per quarter.
3. **Validation script:** ship `scripts/check-dns.sh` that the customer can run inside their worker container to confirm resolution before deploying. Outputs the same captures this case ships with.

(1) and (3) are doc/script changes. (2) is a one-line Dockerfile change in the sample stack.

## What this does not solve

Customers who insist on Alpine for legitimate reasons (existing investment, security policy, etc.) still need the FQDN workaround. The doc captures it; the linter cannot enforce it on customer-side code.
