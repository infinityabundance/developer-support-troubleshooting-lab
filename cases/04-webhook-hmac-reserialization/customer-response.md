Hi,

You were right — your sender is computing the HMAC correctly. The bug is on our side. We were verifying the signature over a re-serialized form of your JSON payload (parsed and re-encoded with our default settings), not over the raw bytes you actually sent. Whitespace, key ordering, and number formatting differ between the two encodings, so HMACs that should match don't.

This is being fixed today. The fix moves the signature check to run on the raw request body before any parsing happens. ETA on the rollout is [time]. Once it lands, your existing sender code will work without changes — no signature scheme change, no secret rotation.

In the meantime, if you need a confirmed-good test event for your own monitoring, I can send one through a temporary verifier that bypasses the buggy path. Reply if you want that.

Thanks for catching this — the explicit "we computed HMAC over the body we sent" line in your ticket is what made it solvable in one round-trip rather than three.
