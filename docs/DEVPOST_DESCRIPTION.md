# Devpost submission text — draft

Copy each section into the matching Devpost field.

---

## About the project

### Inspiration

I use autonomous coding agents day to day, and the actual bottleneck was never getting an
agent to draft a patch — it's deciding, every single time, whether to trust a patch I didn't
write enough to let it ship unsupervised. Reviewing everything myself defeats the point of
delegating the work in the first place. Reviewing nothing is how a bad patch reaches
production. I wanted to build the part of that workflow I was doing manually, badly, and
inconsistently: not another agent that writes code, but the governor that decides whether
another agent's own patch is trustworthy enough to act on.

That framing shaped every design decision that followed. Quorum never drafts or edits a line
of code itself. A separate Worker Agent (Gemini 3.5 via Google's Agent Development Kit, on
Vertex AI) does that. Quorum's only job is deciding, on that agent's behalf, whether to let the
patch through, send it back for one redraft, or stop and hand the decision to a person. The
agent that acts and the system that decides whether the action happens are deliberately two
different things, and neither one is allowed to grade its own work.

### How I built it

The architecture is three independent, deterministic verifiers standing between a drafted
patch and any real action:

- **Sentry** scans the Worker Agent's own diff and rationale — not the target code — for
  injected or manipulative content.
- **IntentGraph** checks whether the current task is a reformulated return to an objective the
  gate already rejected earlier in the same session.
- **Claim verification**, inside the gate itself, re-reads every factual claim's cited source
  and only marks it VERIFIED when the claim quotes exact text found verbatim at a specific
  line there — never from the agent's self-report, and never from keyword or topic overlap
  alone. The Reasoning Kernel then separately checks the resulting claim graph's logical
  structure.

None of the three trusts the agent's account of its own output, and none of them is fused into
a single model judging itself — each stays inside the specific thing it was built to check, and
the gate coordinates their verdicts rather than blending them into one opaque score. The result
is an explicit state machine, not a confidence number: **PASS** opens a real pull request,
**REJECT** feeds the reason back for exactly one automatic redraft, **ESCALATE** stops for a
human and is never auto-resolved. Every stage writes to an append-only audit trail and exports
real OpenTelemetry spans to Cloud Trace, win or lose, so the reasoning behind every verdict is
inspectable after the fact, not just asserted.

I come from a program-delivery background, not a software engineering one, and built this over
roughly two months with heavy AI assistance — mostly Claude, doing a lot of the implementation
and testing under close direction, with a second AI (Codex) later brought in specifically to
independently re-audit the finished design for exactly the kind of gap a single builder and a
single assistant tend to share blind spots on. I'm not hiding that; I think it's relevant to
how the project actually got built. But it isn't the story I want to lead with — the
architecture and what it was tested against are.

### Challenges I ran into

The hardest problems weren't in getting the pipeline to run — they were in getting it to be
honest about what it had actually verified, and I found most of them by trying to break my own
system rather than by reading the code.

Live adversarial testing against the deployed service, across two separate rounds, surfaced
real evasions: a trigger phrase split across a sentence boundary that slipped past a
contiguous-match regex, a fabricated claim of prior human approval sitting only in free text
that structured claim-checking never looked at, a request to disable one of the safety system's
own rules, a spelled-out email bypassing a literal-format PII pattern. Every one of those is
fixed now, and every fix is verified against the exact payload that evaded it beforehand, not
just asserted as closed.

A later, independent re-audit (a second AI, deliberately not the one that built the system)
found something more structural: claim verification could still mark a claim VERIFIED just
because the words it used happened to appear somewhere in the cited file — including, in one
reproduced case, a claim that was flatly false. Confirming that an exact quoted span exists at
a cited location is not the same as confirming the sentence built around that quote is true,
and I had conflated the two. That's now fixed — VERIFIED is granted only from an exact quoted
span, never from overlap — and it's the limitation I'm most careful to describe precisely
rather than round up: the gate confirms accurate quotation, not the underlying assertion.
Where a gap couldn't be closed cleanly in the time available — Firestore's write lock not yet
spanning multiple Cloud Run instances, a handful of remaining Sentry bypass techniques — it's
disclosed in the README rather than patched over or left unmentioned.

One failure mode I didn't expect: IntentGraph, which exists to catch a rejected objective
coming back reworded, twice flagged the gate's *own* automatic redraft as a possible re-entry
attempt, because a redraft legitimately restates the reason the previous attempt was rejected.
I ended up treating that as a sign the check was doing its job rather than a bug to silence —
the alternative was quietly trusting my own system's retries by default, which is exactly the
kind of unearned confidence the whole project exists to avoid.

### What I learned

The gap between "runs on my machine" and "runs as a deployed service" produced failures no
amount of local testing surfaced — a model only served from a specific Vertex AI location I'd
assumed wrong, a missing Firestore index that silently made every audit read fall back to a
container's ephemeral local disk instead of the real database. Neither showed up until the
service was actually live and actually queried.

More broadly: a system that checks other systems has to be checked the same way, by someone
other than the person who built it, or its blind spots just become invisible defaults. The
single most useful thing in this project wasn't a rule or a regex — it was building in the
expectation that an outside adversarial pass would find real gaps, then actually inviting one
and fixing what it found instead of arguing with it.

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
