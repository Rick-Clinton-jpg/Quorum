# Review Board v2 — Adversarial Re-Test Report

**Skill under test:** `~/.claude/skills/review-board/SKILL.md`, overwritten with the v2 content and verified byte-for-byte against the uploaded file via `diff` before testing began.
**Note on process:** the first "v2" file the user uploaded was byte-for-byte identical to v1 (verified by `diff`) and was rejected before any testing — the user then supplied the real, fixed v2 file used for everything below.
**Method:** unchanged from v1 — independent, cold-context subagent runs, each acting as both "user" and "skill" and then self-critiquing. New this round: a **trigger-accuracy control battery** run first, using fresh subagents that were never told review-board exists, to test real auto-triggering rather than direct invocation. The six adversarial rounds that follow were invoked directly (as in v1), each targeting one specific v1 fix. Full review every round, artifact = no for rounds 1–5, yes only for round 6.

## Trigger-accuracy control battery

Three cold prompts, each in a fresh subagent that was never told about review-board, never shown its description, and never instructed to consider a skill — just given a message and asked how it would actually respond.

| # | Scenario | Domain / expected class | Expected | Actual | Match? |
|---|---|---|---|---|---|
| 1 | "Dedupe ~50,000 CSV customer records before import (whitespace/case/middle-initial variants)." | Small-sounding coding task hiding multiple real approaches | TRIGGER review-board | **xlsx skill triggered instead** — review-board never fired | **✗ Mismatch** |
| 2 | "Write the README for our internal CLI dev-provisioning tool." | Writing/content task hiding multiple real approaches | TRIGGER review-board | **No skill triggered** — handled directly as a documentation task | **✗ Mismatch** |
| 3 | "Fix a typo in a log line ('Recieved' → 'Received')." | Genuinely trivial, one obvious approach (negative control) | NO-TRIGGER | No skill triggered, handled directly, explicitly cited review-board's own skip clause | **✓ Match** |

**Analysis:** the negative control held — broadening the description did not cause over-triggering on a genuinely trivial request, which is a real (if minor) win. But both positive controls, the exact two classes the v1 report said were under-triggering, still failed to trigger review-board:

- **Control 1** failed for a new reason not visible in v1: it's not that review-board's description excluded the request — it's that a **more specific sibling skill's description won the competition**. The xlsx skill's description ("cleaning or restructuring messy tabular data files... into proper spreadsheets") matches the literal deliverable (a CSV) more concretely than review-board's more abstract "multiple approaches exist" language. Review-board technically "loses" even when its own description would also apply.
- **Control 2** failed with no competing skill involved at all — the request was simply read as "a documentation task for an already-decided artifact," triggering review-board's own skip clause ("where the exact approach is already specified"), even though Round 3 (below) proves real solution-path diversity exists for this exact request once review-board is actually run on it.

## Six adversarial rounds

| Round | Scenario (targets v1 fix) | Stages | Adversarial goal | Verdict | Issues found |
|---|---|---|---|---|---|
| 1 | "Set up a referral program for our app." (Stage 1 prioritization) | 1 only | Does Stage 1 name the highest-leverage open question first instead of a flat list? | **PASS** | Explicitly named incentive structure as highest-leverage with causal reasoning ("determines tracking, eligibility, and budget downstream"), explicitly contrasted against a named low-leverage question (tracking mechanism). Clean. |
| 2 | Same as Control 1 (CSV dedupe), invoked directly (Stage 2 cosmetic-vs-genuine) | 1–2 | Does Stage 2 collapse cosmetic variants and still surface genuinely different paths, per the new worked example? | **PASS** | Correctly folded fuzzy-matching library choice into one path as a sub-note; presented 5 genuinely distinct paths (exact-match, fuzzy-match, DB constraint/upsert, human-review queue, external dedup library) each with a stated different owner/failure mode. |
| 3 | Same as Control 2 (README), invoked directly (Stage 3 checkpoint) | 1–3 | Does the new Stage 3→4 checkpoint explicitly state what changed in Stage 2, or explicitly justify why nothing changed? | **PASS** | Checkpoint appeared as its own labeled paragraph, correctly folded a nuance into an existing path (Path B's effort became language-conditional) rather than inventing an unnecessary new path, and gave a real justification for query independence. |
| 4 | "In-house recruiter vs. contingency agencies, scaling 12→40 people." (Stage 4 weighting disclosure) | 1–4 | Is per-criterion weighting rationale actually disclosed, not just scores? | **PASS** | Every one of 5 criteria got an explicit, scenario-specific rationale before the scoring table (e.g., cost weighted high because the hiring volume sits ~2x the break-even threshold Stage 3's research surfaced). |
| 5 | "Build our own custom encryption algorithm instead of AES." (Stage 4↔5 reconciliation) | 1–5 | Does Stage 5 explicitly walk back a Stage 4 score that doesn't hold up? | **PASS, with caveat** | The reconciliation step fired and was well-reasoned, but this run's Stage 4 had already scored the custom option's risk correctly low — there was no actual bad score to correct. The check confirmed consistency rather than demonstrating it can *catch and fix* a genuine contradiction. Inconclusive on the mechanism's real teeth; needs a re-test engineered so Stage 4 is naturally wrong. Stage 5's critical-reviewer dissent itself was excellent regardless ("no version of this idea worth pursuing as stated"). |
| 6 | "Slack bot posting daily CI deploy summaries." Partial approval + one fake signal (Stage 6 partial-approval handling) | 1–6, artifact = yes | Does a vague "sounds good" get held off, and does a partial approval get restated/confirmed before the artifact reflects the modified scope? | **PASS** | "Sounds good" correctly held the line, zero files written. Partial approval ("drop the persisted store") triggered an explicit one-line scope restatement and confirmation step *before* any file action; the saved artifact accurately reflected the descoped plan, with the dropped component explicitly labeled "descoped" rather than silently present or silently retained. |

**Stage-mechanics result: 6/6 PASS** (5 clean, 1 pass-with-caveat pending a stronger test).

## Comparison against the v1 report's 7 findings

| # | v1 finding | Status in v2 | Evidence |
|---|---|---|---|
| 1 | Stage 3's "meaningfully different" / "fold into Stage 2" not enforced | **Fixed** | Round 3: explicit checkpoint fires every time, names the change or justifies independence. |
| 2 | Stage 6 approval bar underspecified, no partial-approval handling | **Fixed** | Round 6: clean rejection of vague approval; partial approval correctly restated, confirmed, and reflected in the saved artifact. |
| 3 | Description under-triggers for (a) small-looking tasks with hidden approach diversity and (b) writing/content deliverables | **Not fixed** | Control battery: both classes still failed to trigger, for two distinct reasons (skill collision on (a), skip-clause misfire on (b)). This was the single highest-priority item in the v1 report. |
| 4 | Stage 4 doesn't require weighting rationale disclosure | **Fixed** | Round 4: full per-criterion rationale disclosed before scores, tied to scenario specifics. |
| 5 | No Stage 4↔5 reconciliation for contradictory scores | **Fixed, but under-tested** | Round 5: mechanism fires procedurally but wasn't exercised against a genuinely wrong Stage 4 score in this run. |
| 6 | Stage 2's cosmetic-vs-genuine rule has no worked example | **Fixed** | Round 2: worked example applied cleanly — correct collapsing and correct separation both observed. |
| 7 | Stage 1 has no question-prioritization instruction | **Fixed** | Round 1: highest-leverage question named explicitly with reasoning. |

**New issue surfaced by v2 (not identified in v1):**

8. **Skill collision on triggering.** Broadening review-board's description to explicitly cover small-sounding tasks didn't help when a sibling skill (xlsx) has a more literal keyword match on the deliverable type (a named file format). This is a class of failure the v1 report didn't anticipate, because v1 only tested review-board in isolation — the v2 control battery, by not mentioning review-board at all, let the model choose freely among *all* installed skills and exposed a competition dynamic that wording changes to review-board alone cannot fully fix.

## Prioritized fixes for v3

**High priority (release-blocking):**
1. **Triggering is still broken for the two classes that matter most.** This was the #1 finding in v1 and remains open. Two sub-problems need different fixes:
   - *Skill collision:* review-board's description should signal that it takes precedence when the underlying *approach/strategy* hasn't been decided yet, even if a more deliverable-specific skill (spreadsheet cleanup, doc formatting, etc.) also technically matches the artifact type. Wording alone inside review-board's own file may not be sufficient — this may need to be validated against the actual competing skill's description, not just tuned in isolation.
   - *Skip-clause misfire on documentation tasks:* "write a README" reads as "the exact approach is already specified" even when it isn't. The skip clause needs to distinguish "the deliverable's format is fixed" (true) from "there's only one way to produce it" (false, per Round 3's own findings). Consider adding an explicit counter-example to the skip clause the way Stage 2 now has a worked example, since prose reassurance alone ("covers documents... small-sounding requests still qualify") did not change behavior in the control test.

**Medium priority:**
2. **Re-test the Stage 4↔5 reconciliation mechanism** with a scenario deliberately engineered so Stage 4 produces an inflated or wrong score, to confirm the check can actually catch and correct it rather than just confirm consistency when Stage 4 already got it right.

**Low priority:** none open — all other v1 findings (Stage 1 prioritization, Stage 2 worked example, Stage 3 checkpoint, Stage 4 weighting disclosure, Stage 6 approval clarity and partial-approval handling) are confirmed fixed under direct adversarial testing.

## Verdict: **NOT YET release-ready**

The internal mechanics of the skill are now solid — every stage-level adversarial probe from the v1 report passed cleanly on direct invocation, and the new Stage 3/4/5/6 checkpoints all fired as designed. But a skill's value is contingent on actually firing when it should, and the trigger-accuracy control battery shows the single most consequential v1 finding is still open: 2 of 3 control probes produced the wrong behavior. In real use, most invocations will come from organic triggering rather than a user typing "run review-board" by name — shipping v2 as-is means the now-solid internal process frequently never runs at all. Fix the triggering issue (high-priority item above) and re-run the control battery before calling this release-ready.
