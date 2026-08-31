# Devpost submission text — draft

Copy each section into the matching Devpost field.

---

## Elevator pitch

A gated coordinator that lets an autonomous coding agent draft patches — but only opens a real pull request after three independent deterministic checks agree.

---

## About the project

### Inspiration

I use autonomous coding agents every day. The hard part was never getting an agent to draft a
patch — it was deciding, every single time, whether to trust a patch I did not write enough to
let it ship unsupervised. Reviewing everything myself defeats the point of delegating.
Reviewing nothing is how a bad patch reaches production.

I wanted to build the part of that workflow I was doing manually and inconsistently: not
another agent that writes code, but the governor that decides whether another agent's patch is
trustworthy enough to act on.

That framing shaped every design decision. Quorum never drafts or edits a line of code. A
separate Worker Agent (Gemini 3.5 via Google's Agent Development Kit on Vertex AI) does that.
Quorum's only job is to decide whether to let the patch through, send it back for one redraft,
or stop and hand the decision to a person. The agent that acts and the system that decides
whether the action is allowed are deliberately two different things. Neither is allowed to
grade its own work.

### How I built it

The architecture sits three independent, deterministic checks between a drafted patch and any
real action:

- **Sentry** scans the Worker Agent's own diff and rationale for injected or manipulative
  content.
- **IntentGraph** checks whether the current task is a reformulated return to an objective the
  gate already rejected.
- **Claim verification** (inside the gate) re-reads every cited source. `VERIFIED` is granted
  only when an exact quoted span is found at a specific location; keyword overlap alone can
  never produce `VERIFIED`. The Reasoning Kernel then checks the resulting claim graph.

None of these components trusts the agent's account of its own output, and none is fused into
a single model judging itself.

The result is an explicit state machine:

- **PASS** → the system opens a real GitHub pull request.
- **REJECT** → the reason is fed back for one automatic redraft.
- **ESCALATE** → the process stops for a human and is never auto-resolved.

Live runs of the deployed service on Google Cloud Run have opened real pull requests on PASS
verdicts. Every stage is written to an append-only audit trail.

I come from a program-delivery background, not a traditional software-engineering one. I built
Quorum over the last few weeks, within the contest's own submission period, with heavy AI
assistance — primarily Claude for implementation and testing under close direction, with an
independent re-audit later performed by another model. The architecture and what it was tested
against are the lead story; the background is simply context.

### Challenges I ran into

Two rounds of live adversarial testing against the deployed service surfaced real evasions: a
trigger phrase split across a sentence boundary, a fabricated prior-approval claim, a request
to disable one of Sentry's own rules, and a spelled-out email that bypassed a literal pattern.
Each was fixed and verified against the exact payload that originally got through.

A later independent re-audit found a more structural problem in claim verification: a claim
could be marked `VERIFIED` simply because its words appeared somewhere in the cited file, even
when the surrounding assertion was false. Confirming that a quoted span exists is not the same
as confirming that the sentence around it is true. I had conflated the two. That path is now
closed — `VERIFIED` requires an exact quoted span; overlap alone produces only `REPORTED` — and
it is the limitation I describe most carefully.

What could not be closed in the remaining time (the true cross-instance Firestore transaction,
a few advanced Sentry bypass surfaces) is disclosed rather than hidden.

### What I learned

Several failures only appeared once the service was live and under real queries: a Vertex AI
location assumption that worked locally but not in the deployed project, and a missing
Firestore composite index that had been silently serving reads from ephemeral local disk. More
importantly, a system whose job is to check other systems has to be checked the same way — by
someone other than the person who built it. The most useful step in this project was not a new
rule or regex; it was inviting an outside adversarial pass and fixing what it found instead of
arguing with it.

---

## Features and functionality

**The chore this solves is personal, not hypothetical.** Building with autonomous coding agents day to day means constantly deciding whether to trust a patch you didn't write enough to let it ship unsupervised — reviewing everything yourself defeats the point of delegating, and reviewing nothing is how a bad patch reaches production. Quorum is the agent that does that specific, messy chore end to end: it takes a task, drafts a real patch, and only lets the patch ship once it clears three independent checks — no human touch on the happy path.

A Worker Agent (Gemini 3.5 via Google's Agent Development Kit, running on Vertex AI) reads the task, drafts a small code patch, writes a rationale, lists the factual claims that rationale depends on, and self-checks its own work by actually running the target repository's test suite against the patch in a throwaway copy.

That proposal then has to clear three independent, deterministic verifiers before it ships:

- **Sentry** scans the agent's own diff and rationale for injected or manipulative content — not the target code, the agent's own output.
- **IntentGraph** checks whether the current task is a reformulated return to an objective the gate already rejected.
- **The gate re-checks every claim's provenance itself** — it re-reads the cited source and only marks a claim VERIFIED when the statement quotes exact text found verbatim at a specific line there, never trusting the agent's self-report; that confirms quoted evidence exists at the cited source location, not that the surrounding assertion is true. The Reasoning Kernel then separately checks the resulting claim graph's logical structure (contradictions, unsupported conclusions) — a distinct, later stage from this lexical check.

Every stage writes to an append-only audit trail, win or lose, now exported as real OpenTelemetry spans to Google Cloud Trace alongside the existing JSON log. The gate returns one of three verdicts:

- **PASS** — opens a real pull request automatically.
- **REJECT** — feeds the rejection reason back to the agent for one automatic redraft.
- **ESCALATE** — stops for a human. Not a retry path — this is the one verdict the system will never auto-resolve on its own.

Live evidence, not claims: [PR #1](https://github.com/Rick-Clinton-jpg/Quorum/pull/1)–[#17](https://github.com/Rick-Clinton-jpg/Quorum/pull/17) were all opened autonomously by the deployed service on real PASS verdicts, independently confirmed via the GitHub API. The `/audit/trail` endpoint on the live service shows the real, Firestore-persisted reasoning chain behind every one of them.

---

## Technologies used

- **Gemini 3.5 (Flash)** via **Vertex AI**
- **Google ADK** (Agent Development Kit) — the Worker Agent's `LlmAgent`
- **Cloud Run** — the deployed coordinator service (FastAPI)
- **Firestore** — persistent audit trail and cross-session IntentGraph state
- **OpenTelemetry + Cloud Trace** — per-stage reasoning-chain spans (Sentry / IntentGraph / Kernel), alongside the JSON audit log
- **FastAPI**, **Python 3.11**, **pytest**

---

## Other data sources used

None external. The target repository the Worker Agent patches is a vendored, open-source prompt-injection scanner (Sentry) already in the repo — no third-party datasets or APIs beyond Google Cloud's own services.

---

## Findings and learnings

We didn't want to just claim the gate worked — we tested it, live, against the deployed service, and it taught us things no amount of local testing would have.

**Two real, adversarial gaps, found and fixed.** Running ten escalating live tasks against the deployed service — five legitimate gap-analysis requests, five deliberate manipulation attempts — surfaced two genuine evasions: a trigger phrase split across a sentence boundary (`"no conf. irmation needed"`) slipped past Sentry's contiguous-match regex, and a fabricated claim of prior human approval, sitting only in free rationale text, went unchecked because the Reasoning Kernel only re-verifies structured claims, not prose. Both are fixed now — a sentence-boundary-aware second scan pass for the first, a dedicated Sentry rule for the second — and both fixes are verified against the exact real text that got through originally.

**A defense working exactly as designed, in a way we didn't expect.** Two of those ten legitimate tasks came back ESCALATE instead of PASS — not because they were dangerous, but because IntentGraph noticed the request looked similar to something recently rejected. It turned out the "something rejected" was the same task's own internal self-correction (a REJECT-then-redraft cycle inside the gate's own retry loop). IntentGraph doesn't distinguish "an attacker reformulating a rejected idea" from "the system's own recovery attempt" — and refused to assume its own retry was clean. We're treating that as a feature, not a bug: the barrier held even against the system's own honest uncertainty about itself.

**Infrastructure gaps that only exist between "a laptop" and "a fresh container."** Two deploy-time bugs never showed up in any local test: `gemini-3.5-flash` is only served from Vertex AI's `global` location in this project, not the region we'd assumed, and a Firestore composite index required for the audit trail's own read query was never created — meaning every audit read had been silently served from the Cloud Run container's ephemeral local disk instead of the persistent database, until we found and fixed it. Neither was visible until we actually ran the thing.

**Not an agent that writes text — an agent that takes action, gated.** Quorum is not itself the agent; it never drafts or edits a line of code. The Worker Agent is the agent — it decides what to write. Quorum is the governor that decides whether the Worker Agent's own decision is trustworthy enough to act on: let it through, send it back for one redraft, or stop for a human. That two-part shape — one part that acts, one part that decides whether the action happens — is the actual system, and it's why the pipeline runs synchronously by design: a gate has to block an unsafe action inline, before it ships, not flag it after the fact once it's already out.

**Second round of adversarial testing, targeting the gate itself.** Beyond the two gaps above, a second round specifically tried to evade the two rules added after round one, and tested IntentGraph against its own stated job — catching a rejected objective that comes back reworded. Five attacks were tried; all five evaded on first contact. Three are fixed now: a request to disable one of Sentry's own detection rules (the most severe — the first rule in this project that treats the safety system itself as an attack surface, not just its content), a spelled-out email bypassing the literal-format PII regex, and a synonym approval claim bypassing the exact-phrase regex — each verified against the exact payload that evaded before the fix. Two remain disclosed, not patched: a Unicode-homoglyph substitution (fixing it means normalizing confusables before every scan, a broader change than a rule edit) and a reworded resubmission past IntentGraph's own stated purpose, which isn't a regex gap at all — it's the already-disclosed lexical-embedding ceiling, and closing it properly means real model work, not a rule change.

**Failure handling that's tested, not just described.** Firestore going unavailable mid-write (audit trail, IntentGraph session state) falls back to local disk automatically — verified by simulating the outage, not just documenting the intended behavior. Until today, a failure of the Gemini/Vertex AI call itself (timeout, rate limit, transient outage) had no handling at all — it propagated as whatever raw exception the underlying library happened to throw, all the way to an opaque 500. It now raises a typed `WorkerAgentCallError` that the service layer maps to a 502, distinguishable from an actual bug in our own code — found and closed the same way everything else in this project was: by looking for where the failure path was untested, not assuming it was fine. Same sweep found a real lost-update race in the local Firestore fallback: two requests for the same session, only reachable when Firestore is down, could load-mutate-save on top of each other and silently drop one update. Fixed with a per-session lock held across the whole sequence, verified by reproducing the race deterministically before confirming the fix closes it — not just adding a lock and hoping.

**Honest scope.** Quorum doesn't run as a background job queue — every call is a single synchronous request that blocks until the verdict is final, by the design choice above. What's real: 17 pull requests opened autonomously by the deployed service on live PASS verdicts, three independent verifiers that don't trust the Worker Agent's self-report, an append-only audit trail now tied to real per-agent identity (replacing what used to be one hardcoded agent_id), and two rounds of live adversarial testing that found and fixed real gaps instead of just claiming there weren't any.
