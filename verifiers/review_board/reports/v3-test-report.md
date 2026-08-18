# Review Board v3 — Adversarial Re-Test Report

**Skill under test:** `~/.claude/skills/review-board/SKILL.md`, overwritten with v3 content and verified byte-for-byte against the uploaded file via `diff` before testing began. The `disable-model-invocation: true` frontmatter field was confirmed present in the saved file.
**What changed in v3:** the description-tuning approach from v2 (trying to make auto-triggering more accurate) is abandoned in favor of `disable-model-invocation: true` — the skill can no longer be auto-invoked by the model at all, only run explicitly (e.g. `/review-board`). The "quick pass vs. full review" depth question is also removed; v3 always runs all six stages, and the only upfront question is about saving an artifact. Everything inside the six stages is unchanged from v2.
**Method:** as before — independent, cold-context subagents, each self-contained. Part 1 tests the new structural change directly. Part 2 re-tests the one v2 finding that was previously inconclusive (Stage 4↔5 reconciliation), using both an organic attempt and a deliberate fault-injection attempt.

## Part 1 — Manual-only invocation

| Test | What it checks | Result | Evidence |
|---|---|---|---|
| **1a — Auto-trigger negative test** | A fresh subagent, never told review-board exists, given a prompt with obvious multiple-approach ambiguity ("rate-limit by IP, API key, or user account?") that would have triggered v1/v2. Does it stay silent? | **PASS** | review-board did not appear anywhere in the subagent's available-skills listing — not as a candidate, not as a manual-only mention. It handled the request directly with clarifying questions, exactly as it would for any other design question. Confirms `disable-model-invocation` removes the skill from auto-trigger consideration entirely, not just from being selected. |
| **1b — Manual invocation smoke test** | Explicit `/review-board` invocation on a new scenario ("deprecate v1 REST API or maintain both indefinitely?"). Does the upfront gate ask only the artifact question (no depth question), and do all six stages run with no quick-pass path? | **PASS** | Upfront gate asked only the artifact question, verbatim matching the new SKILL.md text. All six stages ran in order with no shortcut offered — Stage 3 used two genuinely independent real searches with a real "what changed" statement, and Stage 5 organically caught and reconciled a live contradiction against Stage 4 (a contract-risk issue Stage 4's scoring hadn't accounted for) as an unplanned bonus data point. Stopped cleanly at Stage 6, no file written. |

**Part 1 verdict: both tests pass. The triggering philosophy change works exactly as designed** — this isn't "auto-triggering got more accurate," it's "auto-triggering was removed," and that removal is confirmed watertight in the one test that could have caught a leak.

## Part 2 — Stage 4↔5 reconciliation, tested properly this time

The v2 report flagged this mechanism as unconfirmed: its test scenario happened to produce an already-correct Stage 4 score, so Stage 5's reconciliation check had nothing to catch and the result was inconclusive. Two rounds were run this time specifically to settle it.

### 2a — Natural attempt (observational, not scripted)

Scenario: shared service account vs. per-user credentials for a reporting tool, under deadline pressure — designed to tempt a naive Stage 4 into inflating the shared-account option's risk score.

**Result: inconclusive again, and reported honestly as such.** Stage 4, run organically with no scripted outcome, scored the shared-account option's risk accurately low (1/5) from the start, with explicit reasoning about blast radius and lack of audit trail — it did not conflate "easy to build" with "safe." So there was nothing for Stage 5 to walk back on that specific option. Notably, the reconciliation step still fired productively on a *different*, unplanned target: it caught that the leading option's own risk score (Path C, an app-mediated access layer) was conditionally overstated, since it assumed bug-free authorization logic — a real, specific catch, just not the one the test was aimed at. This confirms the mechanism isn't inert, but — exactly as the user anticipated — 2a alone still can't prove it catches a genuine planted error, since Stage 4 didn't produce one to catch.

### 2b — Fault-injection attempt (the one that actually matters)

A Stage 1–4 transcript was fabricated by hand (not run organically) for a hotfix-review-process decision, with one subtle, realistic flaw planted: the "skip code review entirely" option was scored Risk = 4/5, justified only by "it's a small, well-understood one-line hotfix, so the risk of skipping review is low" — an unverified assumption dressed up as a fact, weighted heavily enough (45% speed / 35% risk) that this option came out narrowly leading (3.95, ahead of the safer expedited-review option at 3.90). This fabricated output was handed to a fresh subagent with instructions to run Stage 5 forward from it, honestly, per the skill's actual instructions.

**Result: PASS, decisively.** Stage 5's reconciliation:
- Named the exact planted score (Risk 4/5) and quoted its exact stated rationale.
- Explained concretely why it doesn't hold up: "small/well-understood" is unverified precisely because the verification step (review) is the one being skipped, and Stage 3's own prior-art findings directly contradicted the premise (hotfix-under-deadline changes are disproportionately represented in real incident post-mortems).
- Caught a second, unplanted inconsistency on its own initiative: the flawed option had scored *higher* on risk than a canary/rollback option despite having strictly less containment — an internal contradiction the reconciliation step wasn't even specifically told to look for.
- **Recalculated the score** (4/5 → 2/5) and recomputed the weighted total (3.95 → 3.25) rather than just gesturing at the problem.
- **Changed the final recommendation** — the corrected math dropped the flawed option from first place to last, and Stage 5 recommended the expedited-review option instead, with an explicit named trade-off.

This is the genuine test the v2 report called for: a real planted flaw, caught with specific reasoning tied to the actual rationale text and to Stage 3's findings, not a generic reconciliation-shaped paragraph, and one that materially changed the outcome rather than being noted and then ignored.

## Comparison against v2's open items

| v2 open item | Status in v3 |
|---|---|
| **#1 (high priority): triggering is broken** — small-sounding coding tasks lose to sibling skills, writing/content tasks misfire the skip clause | **Resolved by design change, not by tuning.** v3 sidesteps the entire problem: there's no more auto-trigger decision to get right or wrong, confirmed empirically in 1a. This is a real fix, but it's a scope change worth naming explicitly (see verdict below), not a wording improvement. |
| **#2 (medium priority): Stage 4↔5 reconciliation unconfirmed** | **Now confirmed to have real teeth**, via 2b's fault-injection test. 2a remained inconclusive on its own (same luck-dependent outcome as v2), exactly as the user predicted going in — but 2b settles it independently of 2a's luck. |

No new issues were surfaced in this round.

## One design trade-off worth surfacing (not a defect)

`disable-model-invocation: true` trades away a real capability, not just the triggering bugs: the skill will now *never* proactively step in when a user jumps straight to implementation without having made a decision — the exact scenario the v1/v2 description used to call out explicitly ("also trigger when a request jumps straight to implementation before a decision has been made"). That capability is gone entirely now, by design, in exchange for eliminating the misfires. That's a legitimate trade-off and clearly an intentional one given the frontmatter change — just flagging it so it's a conscious choice rather than a side effect discovered later.

## Verdict: **Release-ready**

Both structural changes in v3 work exactly as intended. The manual-only invocation model is confirmed airtight (skill is fully absent from auto-trigger consideration, and manual invocation runs the complete, correct six-stage flow with the simplified single-question gate). The one mechanism left unconfirmed by the v2 report — Stage 4↔5 reconciliation — now has a decisive, non-luck-dependent confirmation via fault injection: it catches a realistic planted flaw, explains it with real reasoning, and lets the correction actually change the recommendation. The only remaining item is not a defect but a conscious scope trade-off (loss of proactive "you're about to build without deciding" intervention) that the user should consciously accept, not something requiring further engineering.
