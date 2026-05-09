Hi,

Confirmed — the 500s on `/audit` are on our staging side. A schema change that should have been applied to staging when it shipped to production wasn't. Production still works because the change is in place there; staging serves 500s on the affected endpoint until we catch up.

Two paths, your call:

1. If your deploy pipeline owns migrations against our staging environment, re-run it. The 500s will clear within a minute of the migration completing.
2. If you'd rather we apply it directly from our side, reply and we'll do it now. Five minutes including verification.

This won't affect production, and there's nothing on your application code or your integration that needs to change.

We're using this ticket as the trigger to put a guard in place so a behind-on-migrations node refuses to serve traffic next time rather than silently 500. I'll share the tracking ID for that work back here once it has one. The immediate fix won't wait on it.

Reply with which path you want.
