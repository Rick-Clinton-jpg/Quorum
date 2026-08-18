# Reasoning Kernel (RK) v1.0

Deterministic enforcement of the eight integrity rules from TLOR Document 9.

**Files:** `kernel.py` (implementation) · `kernel_demo.py` (every rule, pass and fail)

```
python kernel_demo.py
```

---

## What this is, and what it isn't

The kernel does not reason. It decides whether a proposed change to a reasoning
graph is legal. Modules propose; the kernel disposes.

It is **not** the RVM, the Semantic Compiler, or the reasoning modules. Those need
an LLM and therefore cannot be deterministic. The kernel is the part that can be —
which is why it is the part worth building first.

Nothing here calls a model. Every decision is a dict lookup, a graph traversal, or
a float comparison.

---

## The enforcement model

Rejecting everything invalid is useless when the producers are language models.
Three severities:

| | Behaviour |
|---|---|
| **REJECT** | change refused, transaction rolls back |
| **REPAIR** | admitted in downgraded form, downgrade logged as an event |
| **WARN** | admitted, flagged |

**REPAIR is the important one.** TLOR Rule 1 says a fact without provenance
*becomes* an assumption — a repair, not an error. The claim survives; it just loses
the right to be cited as fact. This is what makes the kernel usable by imperfect
producers rather than a wall they bounce off.

---

## The eight rules, as implemented

| Rule | Enforcement | Severity |
|---|---|---|
| 1. Facts cannot be invented | FACT with no provenance, or origin `GENERATED`/`INFERRED`/`REPORTED`, is demoted to ASSUMPTION | REPAIR |
| 2. Unknown remains unknown | UNKNOWN carrying a confidence value | REJECT |
| 3. Confidence never appears from nowhere | derivation `ASSERTED` rejected; conclusion confidence capped at its support ceiling | REJECT / REPAIR |
| 4. Provenance is immutable | stripping provenance refused; changes logged | REJECT |
| 5. Every conclusion is traceable | CONCLUSION with no SUPPORTS or DEPENDS_ON path | REJECT |
| 6. Contradictions are preserved | contradicted nodes marked contested; deletion refused | REPAIR / REJECT |
| 7. Graph identity is stable | id changes refused; labels may change freely | REJECT |
| 8. No silent mutation | every change is an event; SUPPORTS/DEPENDS_ON cycles rejected; delete becomes archive | REJECT |

Verified in the demo — each rule shown passing and failing.

---

## Rule 1 in practice

Origins map onto nlang's `92xx` evidential markers:

| Origin | nlang | Can support a FACT? |
|---|---|---|
| `RETRIEVED` | 9206 verified against source | yes |
| `USER_INPUT` | — | yes |
| `VERIFIED` | 9206 | yes |
| `INFERRED` | 9201 | no |
| `REPORTED` | 9202 | no |
| `GENERATED` | 9207 generated-not-retrieved | **no** |

A model asserting "average rent in Chennai is 18000" from its own weights produces
a node with origin `GENERATED`. The kernel demotes it to ASSUMPTION and logs
`NODE_DEMOTED`. The information is not lost; its status is corrected.

This is the single most useful rule in the set, and it is the one that connects
directly to Hal.

---

## Rule 3 and confidence propagation

A conclusion cannot be more confident than what it rests on. The kernel computes a
ceiling from supporting nodes and caps anything above it.

```
CONCLUSION  quitting is financially safe [0.95 claimed]
  ASSUMPTION  monthly expenses stay flat [0.42]
  HYPOTHESIS  runway exceeds 9 months    [0.40]

  -> capped to 0.40
```

**Two propagation modes, neither of which is correct in general:**

- `min` (default) — treats supports as perfectly correlated. Conservative about
  decay, so long chains stay usable.
- `product` — treats them as independent. Decays fast; over five or six hops
  everything approaches zero and the numbers stop being informative.

Real support is somewhere between, and the right answer depends on whether your
supporting claims share sources. `min` is the safer default for chains of any
depth. If you need calibrated numbers rather than ordering, neither mode gives
them and you would need a proper Bayesian treatment.

---

## Rule 6 is a Truth Maintenance System

Preserving both branches of a contradiction until evidence resolves them is the
core of **justification-based truth maintenance** — Doyle 1979, de Kleer's ATMS
1986. The kernel implements a simplified version: contradicted nodes are marked
`contested` and cannot be deleted, but no automatic dependency retraction happens.

Cite the prior art. Arriving at TMS independently is a good sign about the design;
presenting it as novel is not.

Related literature worth reading before publishing: blackboard architectures
(shared-structure multi-agent editing), Toulmin's argumentation model and Dung's
abstract argumentation frameworks, and W3C PROV-O, which is a published standard
for exactly the provenance-on-facts problem Rule 1 addresses.

---

## Transactions and the event log

```
begin -> mutate -> validate -> repair or rollback -> commit
```

A rejected transaction restores the pre-transaction snapshot exactly. Partial
reasoning never corrupts the graph.

Every mutation appends an event: `NODE_CREATED`, `NODE_DEMOTED`,
`CONFIDENCE_CAPPED`, `PROVENANCE_STRIP_REFUSED`, `TRANSACTION_ROLLED_BACK`, and so
on. The log is the audit trail, and it is append-only by construction — Rule 8 is
not a policy, it is the only way to change anything.

Permissions are per-module: `READ WRITE VERIFY ARCHIVE LOCK DELETE`. A module
without WRITE cannot create nodes, and the denial is logged.

---

## Where this sits relative to the rest

```
nlang packet  ──> router.py ──> kernel.py ──> graph
                  (transport)    (integrity)
```

The router validates messages in flight. The kernel validates the reasoning
structure those messages build. Same principle in both: **the models propose, and
something deterministic decides.**

Not built: a bridge that ingests nlang packets directly as kernel nodes. The
mapping is obvious (`9604` FACT slot → FACT node, `9206` → `Origin.RETRIEVED`,
`431x` → confidence) but it is not written yet.

---

## Honest limitations

**The compiler problem is untouched.** The kernel validates graphs; it does not
build them. Whatever produces the graph from natural language is an LLM, and every
ambiguity TLOR set out to eliminate lives in that step. A perfectly valid graph
can be a perfectly wrong reading of the input, and the kernel will pass it. This
is the central unsolved problem in the whole stack and no amount of kernel work
addresses it.

**Confidence numbers are ordinal, not calibrated.** The propagation modes give you
a defensible ordering, not a probability. Do not present capped values as
likelihoods.

**No persistence.** Graphs live in memory. No store, no versioning across sessions,
no branching or merge.

**Rule 6 is simplified.** Real TMS retracts dependent conclusions when a
justification fails. This marks contested nodes and stops.

**No scale testing.** Cycle detection is iterative and fine, but validation is
O(nodes x edges) per commit and has not been run on anything large.

---

## The bridge — nlang packets into the graph

`bridge.py` ingests a router-parsed packet as kernel nodes. `pipeline_demo.py`
runs three agents end to end.

```
agent ──> router.py ──> bridge.py ──> kernel.py
          transport      mapping       integrity
```

### Mapping

| nlang slot | Kernel node |
|---|---|
| `9600` goal, `9601` intent | GOAL |
| `9602` entity, `9610` context | OBJECT |
| `9604` fact | FACT |
| `9605` evidence | EVIDENCE |
| `9606` assumption | ASSUMPTION |
| `9607` hypothesis, `9615` risk | HYPOTHESIS |
| `9608` constraint | CONSTRAINT |
| `9609` unknown, `9613` question | UNKNOWN |
| `9612` result, `9614` decision | CONCLUSION |
| `9611` confidence | *modifies a sibling, not a node* |

Evidential markers become origins. `9206` → RETRIEVED, `9207` → GENERATED,
`9202` → REPORTED. Only RETRIEVED, USER_INPUT and VERIFIED can back a FACT, so a
`9207` fact is demoted on arrival. Confidence comes from the `431x` certainty
axis: `4317` → 0.78.

### Node ids are content hashes

Kernel Rule 7 requires stable identity. If agents 1 and 3 independently assert
the same fact, that must be one node. The id is `sha1(type + codes + label)[:6]`,
so the same claim produces the same id across any number of agents and hops.
Verified in the demo: agent 2 resends agent 1's packet and creates zero new nodes.

A counter would have silently duplicated.

### Three things to be careful about

**Inferred support edges are a liability.** A packet lists slots; it does not say
which fact supports which conclusion. With `infer_support=True` the bridge draws
SUPPORTS edges from every fact and assumption to every conclusion in the packet —
structure the sender never stated. It is **off by default** and should stay off
wherever the sender can state edges explicitly with `9616` + `9309`. This is the
compiler problem reappearing one layer down, and it is worth recognising it in
your own code rather than only in TLOR's.

**Unmarked confidence is treated as asserted.** A packet reporting confidence
with no evidential marker anywhere gets derivation `ASSERTED`, and Rule 3 rejects
the transaction. Strict on purpose — it is the only thing that makes agents mark
evidentials. `strict_confidence=False` downgrades to REASONING.

**Rule 3 fires before Rule 1 can repair.** An unsourced fact is demoted to
assumption. An unsourced fact *that also claims a confidence number* is rejected
outright, because a number with no basis has nothing to demote to. Both paths are
in the demo. This interaction was not designed; it emerged from running the code,
and it is the correct behaviour.

### Found while building this

nlang had no way to point at an existing graph node — every packet was an island.
Added `9309` NODE-REF-FOLLOWS (lexicon v3.1.0). Without it, cross-packet edges
are impossible and the graph can never be more than one message deep.

Also fixed: packet-level evidential status was leaking sideways, so an
unevidenced ASSUMPTION in the same packet as a verified FACT came out labelled
`EVIDENCE`. Assumptions and hypotheses are now always `REASONING` by definition.
