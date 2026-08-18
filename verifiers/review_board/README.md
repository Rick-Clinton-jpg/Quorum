# Review Board

## What this is

Review Board is a Claude Code skill for structured deliberation before committing to an approach. Invoked explicitly, it walks through six stages: restate the objective, map every realistic solution path with trade-offs, run a mandatory second-pass prior art check with an independently-worded search, score the leading options against explicit, disclosed criteria, gather a six-angle multi-perspective review with a reconciliation check against the earlier scoring, and stop for explicit approval before anything gets built, written, or shipped.

## Design philosophy: manual invocation only, by design

This skill does not auto-trigger. It only runs when explicitly invoked with `/review-board`.

That was not the original design. Early versions tried to tune the skill's trigger description to fire automatically on ambiguous or multi-approach requests. Adversarial testing found that approach unreliable in two different ways: it lost to more specific sibling skills even when its own description technically applied, and it misfired on its own skip clause, reading requests as "already decided" when they weren't. Chasing that asymptote — tuning wording to close both gaps — did not hold up under a second round of testing.

Rather than keep tuning, the skill was changed to disable model invocation entirely. The skill's value is the reliability of the deliberation it produces when it runs, and an ambient trigger that fires correctly only about one time in three undermines that trust more than it adds convenience. So it never fires on its own — invoke it explicitly with `/review-board`.

## Test history — the short version

**v1** built the six stages and adversarial testing found real gaps in the internal mechanics: an unenforced prior-art independence check, a vague approval bar at the final gate, and no requirement to disclose scoring rationale.

**v2** fixed those internal mechanics, but the fix for auto-triggering didn't hold. A control battery of cold prompts — requests never labeled as review-board scenarios — showed it still failed to trigger on two real task classes, for two different reasons: losing to a more specific sibling skill, and misfiring on its own skip clause.

**v3** abandoned auto-triggering as a design goal entirely (see above) and fixed the remaining internal gaps. One mechanism was still unconfirmed after v2: whether the multi-perspective review in Stage 5 can actually catch and correct a wrong score from Stage 4, not just confirm a score that was already right. That was settled with a fault-injection test — a subtle, realistic scoring error was planted by hand into a fabricated transcript, and the skill caught it, named the exact flawed rationale, recalculated the score, and changed the final recommendation. The same result was repeated successfully on a second, independent scenario.

Full round-by-round reports are in [`reports/`](reports/), in order.

## On how this was built

I designed this skill. All four rounds of relapse testing — checking whether issues fixed in a prior version actually stayed fixed — were run by AI agents (Claude Code and Kimi) under my direction: I wrote the test plans and pass/fail criteria for each round, judged the results, recognized when a test method was insufficient (the original Stage 4↔5 reconciliation test was inconclusive because it depended on luck, not on whether the mechanism actually worked), and designed a better one (fault injection) to get a real answer. I iterated the spec across three versions based on what each round found. The test reports themselves were written by Claude Code.

This is disclosed here for the same reason the skill itself insists on disclosed reasoning rather than an unexplained verdict.

## Installation

**Manual:** copy `SKILL.md` into `~/.claude/skills/review-board/SKILL.md` for a personal skill available across all projects, or into `.claude/skills/review-board/SKILL.md` within a specific project to scope it there. Invoke with `/review-board`.

**Via plugin marketplace:** this repo is also registered as a Claude Code plugin marketplace.

```
/plugin marketplace add Rick-Clinton-jpg/Review-Board
/plugin install review-board@review-board
```

Note: plugin-installed skills are namespaced by plugin name, so the installed command is `/review-board:review-board`, not the bare `/review-board` shown above. The manual install path is the one that gives you the bare `/review-board` command.

## License

[PolyForm Noncommercial 1.0.0](LICENSE).
