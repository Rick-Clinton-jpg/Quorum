---
name: review-board
disable-model-invocation: true
description: Structured six-stage deliberation before committing to an approach. Restate the objective, map every solution path with trade-offs, check prior art with a mandatory second independently-worded search pass, score options against disclosed criteria, gather multi-perspective review, then pause for explicit approval. Invoke directly with /review-board when facing a decision with multiple plausible approaches and you want structured comparison before committing.
---

# Review Board

A structured pre-build review that stops before committing to an approach the options haven't actually been weighed. Unlike open-ended "ask clarifying questions" skills, Review Board produces a specific sequence of deliverables and always ends in an explicit go/no-go decision from the user — nothing gets built, written, or shipped until that decision is made.

Best fit: decisions with more than one real option. If there's clearly one path forward, this adds ceremony without adding insight — you're the one who decides whether that's the situation, not the skill.

## Before starting: ask one thing

Ask up front, before doing any research:

**Artifact** — whether they want a saved planning document (markdown) at the end, or want this to stay purely conversational. Don't create a file unless they say yes.

Wait for the answer before proceeding.

## The six stages

Run all six in order. No quick-pass option — invoking this skill means running the full protocol.

### Stage 1 — Restate the objective

One sentence stating what's being built and why. List explicit constraints and assumptions being made. Confirm this matches the user's actual intent before continuing — if anything is ambiguous, ask now rather than guessing. If more than one question is genuinely open, name which one is highest-leverage to resolve first rather than listing them as equally weighted.

### Stage 2 — Map solution paths

Lay out every realistic approach, not just the first one that comes to mind. For each: a one-line description, pros, cons, and rough complexity/effort. Include at least one path the user hasn't already mentioned, if a genuinely different one exists. Two superficially different options that are really the same approach with cosmetic changes don't count as separate paths — collapse them into one path with a sub-note instead.

What counts as genuinely different: different failure modes, different owners/maintainers, or different underlying trade-offs (not just different implementation details of the same idea). Example: for "add caching to a slow endpoint," an in-process cache and a Redis-backed cache are cosmetic variants of one path (application-tier caching) — note the variation, don't list them as separate paths. A precompute/materialized-view approach and an edge/CDN cache, by contrast, are genuinely different paths from that one and from each other: different data-freshness guarantees, different failure modes, different teams typically own them.

### Stage 3 — Prior art check

Search for existing tools, libraries, projects, or writing that already solve this or something close to it. Run a first search, then reformulate the query with meaningfully different terms — a different angle on the problem, not a synonym swap — and run a **second, independently-worded search pass**. This is the step most reviews skip, and it reliably surfaces things the first phrasing missed.

Before moving to Stage 4, state explicitly what changed in the Stage 2 list as a result of the second pass. If nothing changed, say so directly and explain why the two queries were still meaningfully independent — an unchanged list after two genuinely different searches is a legitimate outcome, but it should be the stated result of a real check, not the default when the second query wasn't actually independent.

### Stage 4 — Score against criteria

Evaluate each solution path from Stage 2 against explicit metrics: usefulness, implementation difficulty, risk, and scalability at minimum, plus any task-specific criteria worth naming. Show the scoring, not just a verdict — the user should be able to see why one option beat another.

State the weighting rationale alongside the scores: why does one criterion matter more than another for this specific decision? A score without a stated weight isn't legible — the user should see why the weighting was chosen, not just what it produced.

### Stage 5 — Multi-perspective review

Review the leading option(s) from six angles:
- **Research** — is the underlying approach sound, and is it actually novel given Stage 3?
- **Engineering** — is it buildable and maintainable with the tools/time available?
- **Product** — does it solve the need the user actually has, not just the one they stated?
- **Design** — will the result be usable by whoever it's for?
- **Security** — what could go wrong, and what's the blast radius if it does?
- **Critical reviewer** — steelman the case against doing this at all.

Before finalizing, check the leading option's Stage 4 scores against what surfaces here. If a high score on some criterion doesn't actually hold up under one of these six angles — for example, a strong "control" score that the security or critical-reviewer angle then undermines — say so explicitly rather than letting the contradiction stand unreconciled.

State a recommendation with a confidence level and name the trade-offs it's accepting.

### Stage 6 — Stop and wait

End the turn here. Do not create files, write code, or produce the final deliverable in this message. State clearly that you're waiting for explicit approval, and name what that approval unlocks.

What counts as approval: a clear go-ahead ("yes, build it," "approved," "go with option B"). What does not count: a vague affirmation ("sounds good," "looks fine") with no clear instruction to proceed, a change of subject, or silence — treat all of these as non-approval and ask directly rather than inferring consent.

If approval is partial or conditional ("approved, but drop the Redis dependency," "go ahead, minus the analytics piece"), don't treat it as full sign-off on the original recommendation. Restate the modified scope in one line, confirm it's correct, and only then proceed. If an artifact was requested, it should reflect the modified scope that was actually approved, not the original recommendation.

## Notes

- If the user interrupts mid-process with a decision (e.g., "skip to option B, just build it"), honor it and move straight to Stage 6 rather than insisting on the skipped stages.
- Stage 3's second search pass is not optional — a single phrasing of a prior-art search reliably misses adjacent work, which is exactly the gap this skill exists to close.
- If a saved artifact was requested, write it once Stage 6 approval is given, not before — the doc should reflect what was actually approved, not a draft that might change.
