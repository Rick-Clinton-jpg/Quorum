---
name: reasoning-kernel-check
description: Mechanically check a piece of analytical reasoning (a decision, argument, or conclusion with supporting claims) for unsupported facts and overconfident conclusions, by building a typed reasoning graph and running it through this project's real Kernel.commit() — not a model self-assessment. Use when asked to fact-check a piece of reasoning, audit a conclusion's evidence, check whether a confidence value is earned, or verify a decision write-up before it's trusted.
---

# Reasoning Kernel Check

This skill runs a real `Kernel.commit()` from `${CLAUDE_PLUGIN_ROOT}/kernel.py`
against a reasoning graph built from the scenario you're given. The kernel is
not a reasoning engine — it doesn't judge whether the argument is *correct*.
It enforces eight structural rules deterministically: no fact without
verifiable provenance, no confidence from nowhere, no conclusion exceeding
the confidence of what supports it, no silent deletion of contradictions,
and more. See `${CLAUDE_PLUGIN_ROOT}/KERNEL.md` for the full rule set.

## When to use this

The input is a scenario or write-up containing one or more claims and a
conclusion or decision resting on them — a case for a promotion, a
purchasing decision, a diagnosis, an investment thesis, an argument in a
document. The goal is to check whether the conclusion's stated confidence
is actually earned by what backs it, and whether any claim treated as
established fact is really just an assertion.

## Process

1. **Decompose the scenario into nodes.** For each claim, decide its type:
   - `FACT` — verifiable, with a source. Must carry `Provenance(source, origin)`
     where `origin` is `RETRIEVED`, `USER_INPUT`, or `VERIFIED` to survive as
     a fact — `GENERATED`, `INFERRED`, or `REPORTED` (hearsay, secondhand)
     get demoted to `ASSUMPTION` by Rule 1. A `FACT` node also *requires* a
     `Confidence` at construction time (`Node.__post_init__` raises
     `FactConfidenceError` otherwise) — score it honestly from how solid the
     sourcing actually is, not from how confidently the scenario states it.
   - `ASSUMPTION` — plausible but not independently verifiable (hearsay,
     unconfirmed anecdotes, things "someone mentioned").
   - `UNKNOWN` — genuinely not addressed by the source material. Must never
     carry a `confidence` value (Rule 2 rejects that outright).
   - `HYPOTHESIS` / `CONCLUSION` — the claim or decision the reasoning is
     building toward. Give it the confidence the scenario actually asserts —
     the kernel's job is to check whether that number holds up, not to
     pre-correct it.
   - Leave out claims that are real but orthogonal to the question (e.g. a
     fact that matters to the decision-maker but isn't evidence for or
     against the conclusion). Wiring everything into a support edge just to
     use it is dishonest — see the worked cases in `examples/` for concrete
     examples of this judgment call.

2. **Connect them with edges.** `SUPPORTS` for claims backing the
   conclusion, `CONTRADICTS` for claims that cut against it (contradictions
   are preserved and marked, never dropped — Rule 6), `INCONCLUSIVE` for
   evidence that exists but doesn't resolve the claim either way (the
   correct edge for an honest "not enough information" case), `DEPENDS_ON`
   for a node that structurally rests on another.

3. **Build and commit the graph:**

   ```python
   from kernel import (
       Kernel, Node, Edge, Provenance, Confidence,
       NodeType, EdgeType, Origin, Derivation, Perm, KernelReject,
   )

   k = Kernel()
   k.grant("checker", {Perm.READ, Perm.WRITE})
   k.begin("checker")

   k.add_node("checker", Node(
       "F1", NodeType.FACT, "her team has the lowest turnover in the company",
       provenance=Provenance("HR, confirmed against payroll data", Origin.VERIFIED),
       confidence=Confidence(0.9, Derivation.EVIDENCE)))
   # ... one add_node per claim ...

   k.add_edge("checker", Edge("F1", "C1", EdgeType.SUPPORTS))
   # ... one add_edge per relation ...

   try:
       findings = k.commit()
   except KernelReject as e:
       findings = e.violations   # hard rejects — graph was rolled back

   for f in findings:
       print(f)
   print(k.trace("C1"))
   ```

   `commit()` applies REPAIRs in place (a FACT with bad provenance becomes
   an ASSUMPTION; an over-claimed confidence gets capped to its support
   ceiling) and returns every finding. A REJECT (e.g. an UNKNOWN with a
   confidence value, or a CONCLUSION with no support at all) rolls the
   transaction back and raises `KernelReject`.

4. **Report the result** the way the worked cases in
   `examples/reasoning-kernel-check-cases.md` do: which rules fired and why,
   the corrected trace (`k.trace(conclusion_id)`), and — the part a rule
   list alone won't tell you — which specific claim was the bottleneck on
   the final confidence number, and whether it's the vivid, memorable one or
   the boring, well-sourced one. Also name what the kernel structurally
   *can't* check: it verifies that a conclusion doesn't exceed its support,
   not that the inference from that support to the conclusion is sound
   (e.g. correlation treated as causation can pass every rule cleanly).

## Reference material

- `${CLAUDE_PLUGIN_ROOT}/KERNEL.md` — full rule definitions and rationale.
- `${CLAUDE_PLUGIN_ROOT}/examples/reasoning-kernel-check-cases.md` — two
  complete worked cases (one REPAIRED, one CLEAN) with real kernel output,
  showing this exact decomposition process end to end.
- `${CLAUDE_PLUGIN_ROOT}/kernel_demo.py` — a minimal runnable script
  exercising every rule, pass and fail.
