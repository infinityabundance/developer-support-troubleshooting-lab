# Triage: first 30 seconds on any unknown ticket

This is the checklist applied before opening any of the case folders. The point is to compress the first half-hour of every support ticket into a sequence that runs in under a minute.

## 1. Identify the request

Get one of: a request ID, a correlation ID, a timestamp window, or a curl/HTTP example. If none of these are in the ticket, ask for them before doing anything else. Diagnosis without a request ID is guessing.

## 2. Pull the relevant logs

Filter logs by the request ID. If the system does not propagate request IDs, filter by the timestamp window plus the customer's account ID. Look at *all* services in the request path, not just the one that returned the error. The error-emitting service is rarely the failing service.

## 3. Reproduce locally if possible

Try to reproduce against a local instance with the customer's exact request shape. If it reproduces, the problem is deterministic and the rest is mechanical. If it does not reproduce, the problem is environmental and step 4 applies.

## 4. If not reproducible: identify the smallest delta

The customer's environment differs from a working environment in a finite number of ways. Enumerate them: API version, client library version, region, account flags, network path, TLS version, JWT issuer, time of day. Bisect.

**For tickets framed as "intermittent" or "flaky":** do not assume non-determinism. Run the same test 5–10 times from the same state. If the result is identical each time, the failure is *state-dependent* (deterministic), not random — which is much cheaper to diagnose. State variables to enumerate: env, container restart, connection pool state, cache warmth, time of day, customer ID, locale, which load-balanced replica answered. Case 06 is an instance: the customer's "third of the time" was a different `getent` resolution path picked at restart, not a coin flip. State-dependence looks random from outside, but every "random" result has a state cause. Find the variable; the fix is mechanical.

## 5. Form one hypothesis, then disprove it

Write the hypothesis down in one sentence. Pick the cheapest test that could disprove it. Run that test before any other. If the hypothesis survives, escalate the test. If it dies, form the next one. Do not run multiple hypotheses in parallel — you will conflate evidence.

## 6. Separate workaround from fix

A workaround unblocks the customer today. A fix prevents the next ticket. Most cases need both. The customer response talks about the workaround. The engineering escalation talks about the fix.

## 7. Write the case down

Symptom, evidence, root cause, fix. If the next ticket on the same bug arrives in six months, the future engineer should be able to resolve it from the writeup alone. If they would have to ask you, the writeup is incomplete.

A note on log-format contracts. Every diagnostic tool — `make trace`, log queries, dashboards, parsers — assumes a specific shape for the input. If the format changes (a log driver gets reconfigured, a service starts emitting JSON instead of plaintext, a timestamp goes from `Z`-suffixed UTC to local-with-offset), the tool produces garbage silently. When you ship a diagnostic, write down the format assumption next to it. When you change a log format, search for tools that depend on it. Case 06 is an instance of this class — a `getent` and a `dig` against the same name resolve differently because the two libraries have different format assumptions about `/etc/resolv.conf`. The log-format contract is the same shape of trap, one level up.

---

The first seven steps are mechanical. The next four are judgment, and they are the part of the job that doesn't get put in onboarding docs because no one wants to write them down.

## 8. Decide whether to push back on the customer's framing

Customers compress diagnosis into the ticket, and the compression sometimes hides the real problem. "Intermittent" almost always means "deterministic but state-dependent." "We haven't changed anything" means "we haven't changed anything we noticed." "It worked yesterday" should make you ask what *did* change yesterday — not whether the customer remembers changing it. Pushing back is not adversarial; it's the cheapest way to surface the variable that broke the build. Do it early, do it gently, do it specifically: ask for a diff between yesterday's working request and today's failing one, not "are you sure nothing changed?"

The cost of not pushing back is sinking an hour into the customer's stated problem and finding it's not the real one.

## 9. Decide whether to refuse

Some tickets are not yours to fix. The customer's IdP is misconfigured; the customer's reverse proxy is rewriting headers; the customer is calling a deprecated endpoint they were warned about three releases ago. The instinct is to fix it anyway because you can. Resist it sometimes: every minute spent fixing an out-of-scope problem is a minute not spent on a ticket that is actually yours, and quietly absorbing other teams' bugs trains the system to file them with you next time.

Refusing well: name the boundary, name what *would* be on your side if it crossed back over, point at the right team or the right doc, and offer to stay involved if the handoff fails. Refusing badly: closing the ticket with "out of scope, please contact your IdP." That gets escalated.

The two cases where you should not refuse, even when it's technically out of scope: when the customer has no path to the right team without you, and when the bug is an instance of a class you've seen ship before from your side and the customer happens to be the messenger.

## 10. Decide whether to escalate, and to whom

The signal for escalation is not "I can't fix this." It's "fixing this requires a decision someone else owns." Migration drift is the platform team's call about deploy gates. JWT audience-list expansion is the security team's call about which audiences are accepted. N+1 in a hot endpoint is the owning team's call about whether the rewrite is this sprint or next.

Escalate with the writeup attached, the workaround already shipped, and the proposed fix named — not with "please look at this." The receiving engineer should be able to scan the escalation and answer the decision in five minutes. If they have to read the customer's original ticket to figure out what happened, the escalation is incomplete.

Do not escalate to whoever is most senior; escalate to whoever owns the decision. The two are sometimes the same person and often not.

## 11. Decide whether the writeup needs to leave the queue

Most cases stop at step 7. Some cases — the ones where the failure mode is general, where the customer is unlikely to be the only one, where the diagnosis took longer than it should have because the evidence wasn't where you expected it — should also turn into something durable: a runbook entry, a log-line addition, a team-wide post (5 minutes), or a doc patch that means the next person doesn't repeat your hour. The judgment call is which cases. The wrong heuristic is "every case I work on" (you'd never close any of them); the right heuristic is "would I file this same bug myself if I encountered it next week?"

If yes, the writeup leaves the queue. If no, step 7 is enough.
