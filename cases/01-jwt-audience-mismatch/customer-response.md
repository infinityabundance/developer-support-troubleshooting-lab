Hi,

Your tokens are valid. The 401 is on us — our staging verifier is configured to accept a single audience value, and your staging integration is signing with a different one. Nothing is wrong with your secret or your token-minting flow.

Quickest path to unblock you today: mint your staging tokens with `aud="api"` instead of `aud="api-staging"`. One-line change in your token issuer. Your dev integration already does this, which is why dev works and staging doesn't.

If touching the issuer config is awkward on your end, let me know — we have a verifier update queued that will accept either form on staging, and I can hold this ticket open until that lands rather than have you change anything.

Either way, no action needed on the key rotation you mentioned. The signing key is current; the rotation alarm you saw was unrelated.

Reply with which path you want and I'll either mark this resolved on your end or track the rollout for you.
