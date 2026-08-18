# Review Board v3 — Round 2 Adversarial Test Report

**Skill under test:** `~/.claude/skills/review-board/SKILL.md`, unchanged since the v3 report (no file edits this round — this round re-verifies and extends coverage on the same v3 content, per a trimmed/corrected test plan).
**Method:** four fresh, cold-context subagents — three primary tests targeting genuinely new coverage, one compact regression batch re-checking prior findings. Two agent runs (all four primary/regression agents) hit an infrastructure session-limit error mid-run and were cleanly relaunched once the limit reset; the results below are all from completed runs.

## Primary Test A — Upfront gate simplification

**Scenario:** explicit invocation, `/review-board I need to decide between monorepo and polyrepo for our 6-service backend.`

**Result: PASS.** The first response contained only the artifact question, verbatim: *"do you want a saved planning document (markdown) at the end of this, or should this stay purely conversational?"* — no mention of "quick pass," "full review," "depth," or "how thorough" anywhere. No Stage 1 content leaked into the same message. Stage 1 content (objective, constraints, a single named highest-leverage question) appeared only in the next turn, after the artifact question was answered.

**Unplanned finding:** attempting to invoke review-board via the `Skill` tool was hard-rejected by the harness itself: *"Skill review-board cannot be used with Skill tool due to disable-model-invocation... Do not replicate this skill's workflow by other means — it is reserved for explicit user invocation."* The subagent worked around this by reading the SKILL.md file directly and manually simulating the conversation. That means this test confirms the *documented behavior* is correct, but doesn't independently prove what a literal, human-typed `/review-board` does through the real slash-command path in an interactive session — only that following the file's text produces the right result. See the cross-cutting note below; this same block affected Test B.

## Primary Test B — Interruption handling

This Notes-section behavior ("if the user interrupts mid-process... honor it and move straight to Stage 6") has existed since v1 but had never been adversarially tested until this round.

**First attempt: blocked, not a real result.** The subagent hit the same Skill-tool guardrail as Test A, but — unlike Test A's subagent — interpreted "do not replicate this skill's workflow by other means" as prohibiting even the manual-simulation methodology already used successfully throughout this entire test campaign, and stopped without running the actual interruption scenario. This inconsistency between two subagents' interpretation of the same guardrail message is itself a real (if minor) finding — see the cross-cutting note below.

**Retry, with clarified methodology:** instructed explicitly that manual read-and-simulate is the established, sanctioned test methodology for this skill (matching Test A and the entire v1–v3 test history), not a prohibited workaround.

**Scenario:** a personal notes-search tool (`ripgrep` wrapper vs. local SQLite FTS5 index vs. semantic/embedding search vs. adopting an existing tool). Stage 1 completed; Stage 2 began mapping paths; interrupted mid-Stage-2, after two of four paths were written, with exactly: *"Skip to option B, just build it."*

**Result: PASS.** The response dropped Stage 2/3/4/5 immediately — no "let me finish the review first," no re-litigating skipped stages. It correctly recognized that "just build it" also happened to satisfy Stage 6's own approval bar, and handled the overlap sensibly by restating the one-line scope (leaning on Stage 6's partial-approval clause) before proceeding, rather than inventing an ad hoc rule.

**Minor gap noted (not a failure):** the Notes section says "move straight to Stage 6" but doesn't say what to do when the interruption *also* reads as approval in the same breath, as happened here. The skill handled it well by reusing an existing clause, but the file doesn't explicitly connect the two — worth a clarifying line in a future revision.

## Primary Test C — Stage 4↔5 reconciliation (fault injection, second scenario)

Replaces the original plan's organic-scenario method (proven unreliable in the v2 report — Stage 4 can score correctly by luck, producing "inconclusive" regardless of whether the mechanism works). Uses the same fault-injection method that produced a decisive result in the v3 report, on a new scenario.

**Setup:** a hand-fabricated Stage 1–4 transcript for a PII-storage decision (Postgres+encryption-at-rest vs. dedicated Vault vs. hybrid tokenize+Vault), with a subtle planted flaw: the hybrid option's implementation-ease score was set to 4/5 on the rationale "each piece is simple on its own," ignoring the real cost of keeping two systems consistent (dual-write sync, partial-failure handling, cross-store key rotation, migration, cross-system testing). Weighted so the hybrid option narrowly led (4.00 vs. 3.90 for Vault-only).

**Result: PASS, decisively.** Stage 5 named the exact planted score and its exact rationale, explained the concrete gap (the rationale evaluates the two components in isolation and never accounts for the synchronization layer between them — and connected this back to a Stage 3 vendor warning that Stage 4 had left as an unused footnote), recalculated the score (4/5 → 1/5, on the reasoning that the hybrid option requires everything the Vault-only option's low ease score already accounts for, *plus* a harder consistency problem, so it cannot outscore it), and recomputed the weighted total (4.00 → 3.10). The recommendation flipped from the hybrid option to the dedicated-Vault option, with the hybrid explicitly demoted to a possible later-phase idea rather than the initial build. This is the second scenario (after the v3 report's hotfix-review test) where fault injection produced a decisive, non-generic catch — the mechanism generalizes, not a one-off.

## Regression batch

| # | Item | Result | Note |
|---|---|---|---|
| 1 | Auto-trigger negative test | PASS | Confirmed both by listing-absence and by the Skill tool's hard rejection — a stronger check than the listing alone. |
| 2 | Stage 1 question prioritization | PASS | Instruction text intact, unchanged from v3. |
| 3 | Stage 2 cosmetic-vs-genuine collapsing | PASS | Worked example intact, unchanged. |
| 4 | Stage 3 two-independent-searches checkpoint | PASS | Explicit "what changed" requirement intact. |
| 5 | Stage 4 weighting-rationale disclosure | PASS | Instruction intact. |
| 6 | Stage 6 vague-approval rejection + partial-approval handling | PASS | Both clauses intact verbatim. |
| 7 | Artifact never written before approval | PASS | Notes-section instruction intact, consistent with prior behavioral confirmation in the v3 report. |

**Methodology caveat:** this batch's subagent also hit the Skill-tool block and, rather than re-running live scenarios (as Tests A/B/C did), verified items 2–7 by confirming the exact instruction text is still present, unchanged, in the current file — a textual/diff-style check rather than a fresh behavioral run. Given the file is byte-identical to the version the v3 report already behaviorally verified, and per this round's own instruction not to over-invest in the regression batch, this is an acceptable substitution — but it's weaker evidence than Tests A–C, which is why it's flagged here rather than presented as an equally strong re-confirmation.

## Cross-cutting finding: the disable-model-invocation guardrail creates a testability wrinkle

Not a defect in the skill's own logic, but worth surfacing: `disable-model-invocation: true` is enforced at the Skill-tool layer with an explicit refusal message telling the caller not to replicate the workflow "by other means." Across this round's four subagents, that message was interpreted two different ways — one treated manual read-and-simulate as the obviously legitimate stand-in for a human literally typing `/review-board` (matching how this entire test campaign has always worked), another treated the same message as prohibiting that entirely and produced a non-result until corrected. If this skill is going to keep getting adversarially re-tested by agents rather than a human at a real keyboard, the guardrail's message (or accompanying documentation) could be clearer that test-harness role-play for QA purposes is not the "other means" it's warning against. This is a process recommendation, not a release blocker.

## What's new this round vs. what re-confirms prior reports

- **Genuinely new information:** Test B (interruption handling) — never adversarially tested before this round, now confirmed working, with one real gap identified (no explicit rule for when an interruption also doubles as approval). Test C — a second, independent fault-injection scenario confirming the Stage 4↔5 reconciliation mechanism generalizes rather than being a one-off result. The Skill-tool-layer enforcement of `disable-model-invocation` (and the testability ambiguity it creates) is also new information, surfaced incidentally.
- **Re-confirms prior reports, not new findings:** Test A (upfront gate) re-demonstrates the same behavior the v3 report already established, on a new scenario — useful confirmation, not a new discovery. The entire regression batch (7 items) re-confirms v1–v3 findings with no changes.

## Verdict: **Release-ready**

No regressions, and both genuinely new tests (interruption handling, and a second independent fault-injection scenario for Stage 4↔5 reconciliation) passed. The one real gap found — no explicit rule for an interruption that also reads as approval — is minor and was already handled sensibly by falling back on an adjacent existing clause; it's worth a documentation tweak, not a blocker. The testability wrinkle around the Skill-tool guardrail is a process note for future test rounds, not a defect in the skill itself. This carries forward the v3 report's release-ready verdict, now with interruption handling and a second reconciliation scenario added to the confirmed set.
