# Devpost submission text — draft

Copy each section into the matching Devpost field.

---

## Features and functionality

Quorum is a gate an autonomous coding agent has to clear before anything it drafts can ship. A Worker Agent (Gemini 3.5 via Google's Agent Development Kit, running on Vertex AI) reads a task, drafts a small code patch, writes a rationale, lists the factual claims that rationale depends on, and self-checks its own work by actually running the target repository's test suite against the patch in a throwaway copy.

That proposal then has to clear three independent, deterministic verifiers before it ships:

- **Sentry** scans the agent's own diff and rationale for injected or manipulative content — not the target code, the agent's own output.
- **IntentGraph** checks whether the current task is a reformulated return to an objective the gate already rejected.
- **The Reasoning Kernel** independently re-verifies every claim's provenance — it re-reads the cited source itself and only marks a claim VERIFIED if the content actually backs it up, never trusting the agent's self-report.

Every stage writes to an append-only audit trail, win or lose, now exported as real OpenTelemetry spans to Google Cloud Trace alongside the existing JSON log. The gate returns one of three verdicts:

- **PASS** — opens a real pull request automatically.
- **REJECT** — feeds the rejection reason back to the agent for one automatic redraft.
- **ESCALATE** — stops for a human. Not a retry path — this is the one verdict the system will never auto-resolve on its own.

Live evidence, not claims: [PR #1](https://github.com/Rick-Clinton-jpg/Quorum/pull/1)–[#14](https://github.com/Rick-Clinton-jpg/Quorum/pull/14) were all opened autonomously by the deployed service on real PASS verdicts, independently confirmed via the GitHub API. The `/audit/trail` endpoint on the live service shows the real, Firestore-persisted reasoning chain behind every one of them.

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

**Honest scope, not full coverage.** Quorum was built for the Fortified Enterprise Fleet track. We're not claiming to have built all six of that track's pillars — we went deep on three of them (Model Armor-style inline guardrails via Sentry, now including PII-pattern detection; Agent Observability via the OpenTelemetry/Cloud Trace integration; Agent Identity via per-agent API keys that resolve to a real caller identity recorded in every audit entry, replacing what used to be one hardcoded agent_id) and partial on a fourth (Memory Bank's persistence, via Firestore-backed cross-session IntentGraph state) rather than building a shallow, unconvincing version of all six. Agent Registry and a true long-running async Runtime are explicitly out of scope for this demo, not silently missing.
