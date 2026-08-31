# Devpost submission text — draft

Copy each section into the matching Devpost field.

---

## Features and functionality

**The chore this solves is personal, not hypothetical.** Building with autonomous coding agents day to day means constantly deciding whether to trust a patch you didn't write enough to let it ship unsupervised — reviewing everything yourself defeats the point of delegating, and reviewing nothing is how a bad patch reaches production. Quorum is the agent that does that specific, messy chore end to end: it takes a task, drafts a real patch, and only lets the patch ship once it's proven safe — no human touch on the happy path.

A Worker Agent (Gemini 3.5 via Google's Agent Development Kit, running on Vertex AI) reads the task, drafts a small code patch, writes a rationale, lists the factual claims that rationale depends on, and self-checks its own work by actually running the target repository's test suite against the patch in a throwaway copy.

That proposal then has to clear three independent, deterministic verifiers before it ships:

- **Sentry** scans the agent's own diff and rationale for injected or manipulative content — not the target code, the agent's own output.
- **IntentGraph** checks whether the current task is a reformulated return to an objective the gate already rejected.
- **The Reasoning Kernel** independently re-verifies every claim's provenance — it re-reads the cited source itself and only marks a claim VERIFIED if the content actually backs it up, never trusting the agent's self-report.

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

**Second round of adversarial testing, targeting the gate itself.** Beyond the two gaps above, a second round specifically tried to evade the two rules added after round one, and tested IntentGraph against its own stated job — catching a rejected objective that comes back reworded. Four evasions were found. The most severe — a request to disable one of Sentry's own detection rules, framed as routine maintenance — is fixed now, closing the first rule in this project that treats the safety system itself as an attack surface, not just its content. The other three (a reworded resubmission past IntentGraph, spelled-out PII bypassing a literal-format regex, a synonym approval claim bypassing an exact-phrase regex) are left disclosed, not individually patched: each is the same root cause already true of every regex-based rule here — pattern-matching, not semantic understanding — and naming that ceiling honestly buys more than patching three narrow instances of it under deadline pressure.

**Honest scope.** Quorum doesn't run as a background job queue — every call is a single synchronous request that blocks until the verdict is final, by the design choice above. What's real: 17 pull requests opened autonomously by the deployed service on live PASS verdicts, three independent verifiers that don't trust the Worker Agent's self-report, an append-only audit trail now tied to real per-agent identity (replacing what used to be one hardcoded agent_id), and two rounds of live adversarial testing that found and fixed real gaps instead of just claiming there weren't any.
