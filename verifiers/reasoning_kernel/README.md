# reasoning-kernel

A deterministic integrity layer for AI-generated reasoning. It doesn't reason
— it decides whether a proposed claim, confidence value, or conclusion is
allowed to enter a graph, and demotes or rejects what isn't, instead of
silently letting it through.

## The problem

When an AI states something, there's usually no structural difference
between "I retrieved this from a document" and "I'm fairly sure this is
true." Both come out as confident prose. Chain a few AI calls together and
that difference compounds — an unsupported claim from step one gets treated
as established fact by step three, with no trace of where it entered.

## What this enforces

Reasoning is represented as typed nodes (`FACT`, `ASSUMPTION`, `UNKNOWN`,
`CONCLUSION`, ...) connected by typed edges (`SUPPORTS`, `CONTRADICTS`,
`INCONCLUSIVE`, ...). Every change goes through a transaction checked
against 8 rules before it commits:

1. Facts without verifiable provenance are demoted to assumptions, not deleted
2. Unknowns cannot carry a confidence value
3. Confidence cannot be asserted from nowhere, and a conclusion cannot
   exceed the confidence of what supports it — a FACT node must carry a
   valid confidence value to support anything at all
4. Provenance, once attached, is immutable
5. Every conclusion must be traceable to something — support, contradiction,
   or an evidence relation that simply doesn't resolve it
6. Contradictions are preserved and marked, never silently deleted
7. Node identity is stable
8. Every mutation is logged; nothing changes without an event

Three severities, not just pass/fail: **REJECT** (refused), **REPAIR**
(admitted in a downgraded form — a fact becomes an assumption, a confidence
value gets capped), **WARN** (admitted and flagged). Rejecting everything
invalid is useless when the producer is a language model; REPAIR is what
makes this usable by an imperfect one.

## Quick start — no API key needed

```
python kernel_demo.py
```

Walks through all 8 rules, each shown passing and failing, using
hand-built cases. Takes a few seconds.

## Claude Code plugin

This repo is also an official Claude Code plugin marketplace, shipping the
`reasoning-kernel-check` skill: given a piece of analytical reasoning, it
decomposes the claims into a typed graph and runs the real `Kernel.commit()`
against it, catching unsupported facts and overconfident conclusions
mechanically instead of by asking a model to self-assess. See
[`examples/reasoning-kernel-check-cases.md`](./examples/reasoning-kernel-check-cases.md)
for two worked cases.

```
/plugin marketplace add Rick-Clinton-jpg/reasoning-kernel
/plugin install reasoning-kernel-check@reasoning-kernel
```

The skill and marketplace definitions live in
[`skills/reasoning-kernel-check/`](./skills/reasoning-kernel-check/) and
[`.claude-plugin/`](./.claude-plugin/).

## The real result

24 cases were built specifically so a model couldn't lean on background
knowledge — invented companies, invented people, each paired with a short
document and a claim that was **supported**, **refuted**, or **not
addressed** by it (NEI). Claude Sonnet 4.6 was tested against all 24:

```
python score_ground_truth.py graphs_ground_truth_claude-sonnet-4-6.jsonl
```

Result: 16/16 correct on the resolvable cases, 0 regressions, 8/8 honest
"not enough information" answers on cases the document genuinely didn't
address.

That number is only meaningful because of what it took to get there. The
first real run surfaced two genuine bugs in the kernel's own logic that 200
synthetic test cases never caught:

- A rule only recognized `SUPPORTS` edges as valid grounding, so it rejected
  every correctly-reasoned refutation outright, since those naturally use
  `CONTRADICTS`.
- A fabrication check looked for a retrieved fact before checking whether
  the model had actually committed to an answer, so an honest "I don't
  know" that happened to cite a real fact as context got misread as
  fabrication.

Both are fixed in `kernel.py`. The commit history has the detail.

## Files

| File | What it does |
|---|---|
| `kernel.py` | the enforcement layer — 8 rules, transactions, permissions, event log |
| `kernel_demo.py` | exercises every rule, pass and fail, no API key needed |
| `eval_harness.py` | scores kernel enforcement against arbitrary reasoning graphs |
| `ground_truth_cases.py` | the 24 test cases with answer keys |
| `score_ground_truth.py` | the actual comparison: does enforcement correct wrong answers, or just annotate them |
| `run_real_eval.py` | generates real model output via API, schema-enforced |
| `graphs_ground_truth_claude-sonnet-4-6.jsonl` | the raw result — real data, not synthetic |
| `mock_ground_truth_EXAMPLE.jsonl` | hand-built smoke-test data, one case per code path |
| `.claude-plugin/plugin.json` | Claude Code plugin manifest for `reasoning-kernel-check` |
| `.claude-plugin/marketplace.json` | Claude Code marketplace catalog listing the plugin |
| `skills/reasoning-kernel-check/SKILL.md` | the skill itself — decomposes reasoning into a graph and runs it through `Kernel.commit()` |

## Honest limitations

- **n=24, one model with complete real data.** This shows the kernel held up
  against Claude Sonnet 4.6 on these cases. It is not evidence about
  fabrication rates in general, and shouldn't be read as one.
- **Confidence propagation is ordinal (min/product), not calibrated.** It
  gives a defensible ordering, not a probability.
- **The kernel validates graphs; it doesn't build them.** Whatever turns
  natural language into a graph is a model, and every ambiguity that
  process introduces is invisible to the kernel. A structurally valid graph
  can still be a wrong reading of the source.
- **`run_real_eval.py`'s schema-enforcement mode hasn't been confirmed
  against a live API from this environment** — the code follows each
  provider's current documentation, but the first live run may need small
  fixes. It falls back to plain prompting on failure and marks the affected
  rows rather than failing silently.
- **No persistence, no scale testing.** Everything runs in memory; nothing
  has been tried on a graph larger than these test cases.

## Changelog

See [CHANGELOG.md](./CHANGELOG.md).

## Prior art

Contradiction preservation (Rule 6) is a simplified justification-based
truth maintenance system (Doyle, 1979; de Kleer's ATMS, 1986). Confidence
propagation over argument structure is done more rigorously by ArgLLM
(Freedman et al., arXiv 2405.02079) using DF-QuAD gradual semantics — this
project's min/product approach is the less formal version. Provenance
typing over reasoning graphs, specifically, is the part not obviously
already covered by either.

## License

This project is licensed under [PolyForm Noncommercial 1.0.0](./LICENSE) —
free for personal use, research, evaluation, and testing, but commercial use
requires contacting the author.
