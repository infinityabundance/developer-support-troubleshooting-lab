Hi,

The handshake failures from the cold clients are real, and the cause is on our side: our endpoint is sending the leaf certificate without the intermediate alongside it. The clients that were working were validating because they had the intermediate cached from a prior connection; the cold ones can't bridge our leaf to a root they trust.

Nothing on your side needs to change. The half of your fleet that's failing today will succeed against our endpoint as soon as we reload with the corrected cert chain. We'll have that out within the next hour, and I'll reply here once it lands so you can re-run any failed jobs.

If your operations team is already working around this by pinning intermediates into client trust stores, you can roll that back after we ship the fix; you won't need it.

For your own monitoring: the diagnostic for this class of issue is `echo | openssl s_client -connect <host>:443 -showcerts` from a clean client. The "Certificate chain" block in the output should list more than one certificate; if it shows only the leaf, the chain is incomplete on the server side.

Reply if you see anything still failing after our reload, and I'll dig in further.
