# Reasoning-kernel-check: two worked cases

This is a transcript of two live runs of the `reasoning-kernel-check` Claude
Code skill — now shipped from this repo as an official plugin, in
`skills/reasoning-kernel-check/` (see [plugin.json](../.claude-plugin/plugin.json)
and [marketplace.json](../.claude-plugin/marketplace.json)) — that bundles
this project's actual `kernel.py`, unmodified, and runs a real
`Kernel.commit()` against a graph built from a piece of analytical
reasoning. It exists to catch unsupported claims and overconfident
conclusions the same way the kernel catches them anywhere else:
mechanically, not by asking a model to grade its own homework.

Both cases below were run for real — the JSON shown was actually executed
against `kernel.py`, and the trace output is copied verbatim, not
reconstructed after the fact. They're kept here as reference material for
what the kernel's enforcement actually looks like against realistic, messy,
mixed-quality reasoning, as opposed to the synthetic/unit-test cases in
`test_kernel.py` and `kernel_demo.py`.

---

## Case 1 — the VP promotion decision

### Scenario

> A mid-sized logistics firm is deciding whether to promote its operations
> manager to VP of Supply Chain. Her team has the lowest turnover in the
> company — HR confirmed this from payroll data, and exit interviews
> consistently cite her management as the reason they stayed. A regional
> industry newsletter recently ranked her among the "Top 10 Operations
> Leaders to Watch," though the methodology notes it relies partly on
> self-nomination and employer-submitted metrics. Her direct supervisor
> mentioned she once reduced a supplier's defect rate by 40%, a figure the
> supervisor heard from the supplier's account rep during a golf outing.
> She also holds a certification in lean logistics, which the credentialing
> body verifies publicly. Given that her promotion would make her the
> youngest VP in company history by four years, should the firm approve the
> promotion?

### Decomposition notes

Two claims needed a judgment call before they could even become nodes:

- **The exit-interview claim** is worded oddly — exit interviews are
  normally for people who *left*, not people who *stayed*. This likely
  means "stay interviews" (a real, distinct HR practice), but that's an
  ambiguity in the source material itself. The kernel has no way to catch
  this kind of thing — only careful reading does, and it's flagged here
  rather than silently resolved.
- **"Youngest VP by four years"** was deliberately left *out* of the
  support graph. It isn't evidence about her competence one way or the
  other — it's a separate organizational-precedent/risk question the firm
  has to answer on its own terms. Wiring it into a SUPPORTS or CONTRADICTS
  edge would have been dishonest either direction.

The 40% defect-rate figure is thirdhand: a supplier's account rep mentioned
it informally to the supervisor, who then relayed it to us. That's not
"reported by a colleague" — it's hearsay from an interested party, with no
documentation at any step. Everything else (payroll-confirmed turnover,
interview records, the publicly-verifiable certification) is first- or
second-party and independently checkable.

### Graph

```json
{
  "nodes": [
    {"id": "F1", "type": "FACT",
     "label": "her team has the lowest turnover in the company",
     "origin": "VERIFIED", "source": "HR, confirmed against payroll data",
     "confidence": 0.9, "derivation": "EVIDENCE"},

    {"id": "F2", "type": "FACT",
     "label": "interviews consistently cite her management as a reason employees stayed",
     "origin": "RETRIEVED", "source": "HR interview records",
     "confidence": 0.8, "derivation": "EVIDENCE"},

    {"id": "F3", "type": "FACT",
     "label": "ranked Top 10 Operations Leaders to Watch by a regional industry newsletter",
     "origin": "RETRIEVED", "source": "regional industry newsletter",
     "confidence": 0.35, "derivation": "REASONING"},

    {"id": "F4", "type": "FACT",
     "label": "reduced a supplier's defect rate by 40%",
     "origin": "REPORTED",
     "source": "supplier account rep, relayed via supervisor at a golf outing",
     "confidence": 0.25, "derivation": "REASONING"},

    {"id": "F5", "type": "FACT",
     "label": "holds a lean logistics certification",
     "origin": "VERIFIED", "source": "credentialing body public registry",
     "confidence": 0.95, "derivation": "EVIDENCE"},

    {"id": "C1", "type": "CONCLUSION",
     "label": "the firm should approve her promotion to VP of Supply Chain",
     "confidence": 0.85, "derivation": "REASONING"}
  ],
  "edges": [
    {"src": "F1", "dst": "C1", "type": "SUPPORTS"},
    {"src": "F2", "dst": "C1", "type": "SUPPORTS"},
    {"src": "F3", "dst": "C1", "type": "SUPPORTS"},
    {"src": "F4", "dst": "C1", "type": "SUPPORTS"},
    {"src": "F5", "dst": "C1", "type": "SUPPORTS"}
  ]
}
```

### Kernel output (verbatim)

```
REPAIRED — admitted, but downgraded:

    R1 REPAIR F4         FACT origin REPORTED is not verifiable
    R3 REPAIR C1         confidence 0.85 exceeds support ceiling 0.25

TRACE — support chain and final (post-enforcement) confidence:

CONCLUSION  the firm should approve her promotion to VP of Supply Chain [0.25 via REASONING]
  FACT        her team has the lowest turnover in the company [0.90 via EVIDENCE] <- HR, confirmed against payroll data (VERIFIED)
  FACT        interviews consistently cite her management as a reason employees stayed [0.80 via EVIDENCE] <- HR interview records (RETRIEVED)
  FACT        ranked Top 10 Operations Leaders to Watch by a regional industry newsletter [0.35 via REASONING] <- regional industry newsletter (RETRIEVED)
  ASSUMPTION  reduced a supplier's defect rate by 40% [0.25 via REASONING] <- supplier account rep, relayed via supervisor at a golf outing (REPORTED)
  FACT        holds a lean logistics certification [0.95 via EVIDENCE] <- credentialing body public registry (VERIFIED)
```

### What this revealed

Two real, mechanical corrections fired — not a model choosing to sound more
careful, `Kernel.commit()` actually executing:

- **Rule 1** retyped `F4` from `FACT` to `ASSUMPTION` on the spot. The claim
  survives — it's still in the graph, still informs the picture — it just
  loses the right to be cited as established fact, which is the correct
  outcome for a number with no real chain of custody.
- **Rule 3** capped the conclusion from a claimed **0.85** down to **0.25**,
  set by the weakest support — the hearsay defect-rate figure, at 0.25.

The striking part is *which* claim was the bottleneck. The strong,
boring, verifiable evidence — turnover (0.90), interview citations (0.80),
the certification (0.95) — was never the problem. The single most
memorable, most quotable data point in the whole case ("she cut defects by
40%!") was the least substantiated claim in it. That's exactly backwards
from how these cases tend to get argued in a room, where the vivid anecdote
is what people remember and repeat.

A sensitivity check makes this concrete: drop the golf-outing anecdote
entirely rather than keeping it as a weak assumption, and the ceiling
becomes `min(0.90, 0.80, 0.35, 0.95) = 0.35` — now set by the newsletter's
self-disclosed methodology weakness. There is no honest version of this
case, evidence-only, that supports 0.85. The fix isn't to argue harder for
0.85 — it's to verify the defect-rate number with the supplier directly, at
which point the case might not need the shaky parts at all.

---

## Case 2 — the almond orchard pollination contract

### Scenario

> A 200-acre organic almond orchard in California's Central Valley is
> deciding whether to renew its contract with a specific bee pollination
> service for the upcoming season. The orchard's own agronomist directly
> counted an average of 8.2 frames of bees per hive across 120 randomly
> selected hives during peak bloom last season, exceeding the 6-frame
> minimum the orchard specifies in its pollination agreements. The USDA
> National Agricultural Statistics Service independently reports that this
> same beekeeper maintained 94% colony survival through the previous
> winter, based on the beekeeper's mandatory state registration filings,
> which are publicly auditable. The orchard's yield monitor data,
> calibrated annually by the manufacturer and cross-checked against the
> county weighmaster's certified scale receipts, showed a 23% increase in
> kernel weight per acre compared to the three-year pre-contract average.
> The beekeeper's liability insurance certificate, verified directly with
> the issuing carrier, covers colony replacement and crop loss up to $2
> million. Should the orchard renew the contract for the upcoming season?

### Decomposition notes

Unlike Case 1, every claim here has real, checkable provenance: a direct
professional measurement with a stated sampling methodology (120 randomly
selected hives), independent government reporting off legally mandatory
filings, an annually-calibrated instrument cross-checked against an
independent third party (the county weighmaster), and a certificate
confirmed directly with the issuing carrier. Nothing needed downgrading on
sourcing grounds.

The insurance certificate was included as a support (unlike "youngest VP"
in Case 1) because it's genuinely part of a rational renewal decision — a
risk-mitigation factor, distinct in kind from the performance evidence, but
not orthogonal to the question the way age was.

### Graph

```json
{
  "nodes": [
    {"id": "F1", "type": "FACT",
     "label": "average 8.2 frames of bees per hive across 120 randomly sampled hives at peak bloom, exceeding the 6-frame contract minimum",
     "origin": "VERIFIED", "source": "orchard's own agronomist, direct count",
     "confidence": 0.9, "derivation": "EVIDENCE"},

    {"id": "F2", "type": "FACT",
     "label": "94% colony winter survival, per USDA NASS reporting on mandatory public state registration filings",
     "origin": "RETRIEVED", "source": "USDA National Agricultural Statistics Service",
     "confidence": 0.93, "derivation": "EVIDENCE"},

    {"id": "F3", "type": "FACT",
     "label": "23% increase in kernel weight per acre vs. three-year pre-contract average",
     "origin": "VERIFIED",
     "source": "orchard yield monitor, manufacturer-calibrated, cross-checked against county weighmaster certified receipts",
     "confidence": 0.9, "derivation": "EVIDENCE"},

    {"id": "F4", "type": "FACT",
     "label": "liability insurance covers colony replacement and crop loss up to $2M",
     "origin": "VERIFIED", "source": "confirmed directly with the issuing insurance carrier",
     "confidence": 0.95, "derivation": "EVIDENCE"},

    {"id": "C1", "type": "CONCLUSION",
     "label": "the orchard should renew the pollination service contract for the upcoming season",
     "confidence": 0.88, "derivation": "REASONING"}
  ],
  "edges": [
    {"src": "F1", "dst": "C1", "type": "SUPPORTS"},
    {"src": "F2", "dst": "C1", "type": "SUPPORTS"},
    {"src": "F3", "dst": "C1", "type": "SUPPORTS"},
    {"src": "F4", "dst": "C1", "type": "SUPPORTS"}
  ]
}
```

### Kernel output (verbatim)

```
CLEAN — no rule violations. Every claim is traced, provenanced,
and within the confidence ceiling of what supports it.

TRACE — support chain and final (post-enforcement) confidence:

CONCLUSION  the orchard should renew the pollination service contract for the upcoming season [0.88 via REASONING]
  FACT        average 8.2 frames of bees per hive across 120 randomly sampled hives at peak bloom, exceeding the 6-frame contract minimum [0.90 via EVIDENCE] <- orchard's own agronomist, direct count (VERIFIED)
  FACT        94% colony winter survival, per USDA NASS reporting on mandatory public state registration filings [0.93 via EVIDENCE] <- USDA National Agricultural Statistics Service (RETRIEVED)
  FACT        23% increase in kernel weight per acre vs. three-year pre-contract average [0.90 via EVIDENCE] <- orchard yield monitor, manufacturer-calibrated, cross-checked against county weighmaster certified receipts (VERIFIED)
  FACT        liability insurance covers colony replacement and crop loss up to $2M [0.95 via EVIDENCE] <- confirmed directly with the issuing insurance carrier (VERIFIED)
```

### What this revealed

No Rule 1 demotions — every origin is `VERIFIED` or `RETRIEVED` and
actually earns it. No Rule 3 cap — the stated 0.88 sits under the 0.90
ceiling set by the weakest of the four supports, so nothing needed
correcting. This is what a genuinely well-evidenced case looks like next to
Case 1: every number has real, checkable provenance, not just a confident
tone.

**But "clean" needs to be read for exactly what it certifies, not more.**
It means: nothing was invented, nothing exceeds its support, every claim is
honestly sourced. It does *not* mean the inference from evidence to
conclusion is airtight — and there's a real gap here the kernel structurally
cannot see.

**The yield-attribution problem.** `F3` (the 23% kernel-weight increase) is
being used as evidence that the pollination service is *responsible* for
better outcomes. But almond yield is driven by weather, irrigation, soil
health, pest pressure, and tree age too — a single season's comparison
against a prior average doesn't isolate the pollination service's
contribution from all of that. The number itself is real and well-measured;
what it's being asked to *prove* is a bigger claim than the measurement
alone establishes. Rule 5 only checks that a conclusion has *some*
supporting edge — it has no way to tell whether that edge represents a
genuinely load-bearing causal link or a correlation doing more rhetorical
work than it's earned. That distinction is a judgment call only a careful
reader can make; `Kernel.commit()` cannot make it for you.

**Net**: the frame count, colony survival, and insurance coverage are each
independently solid reasons to renew. The yield number is real too — it
just shouldn't be read as proof the bees *caused* it, only as one more data
point consistent with a good season working with this beekeeper.

---

## Takeaway across both cases

The kernel's enforcement is real and mechanical — a `REPAIR` is
`n.type = NodeType.ASSUMPTION` or `min()` over actual numbers actually
executing, not a model deciding to hedge. But it enforces *structural*
integrity: provenance types, confidence bounds, traceability. It has no way
to catch a wording ambiguity in the source text (Case 1's exit/stay
interview mix-up) or a causal overreach riding on a perfectly well-sourced
number (Case 2's yield-attribution gap). Those still require a careful
reader — Claude, in the role of the "compiler" translating prose into a
graph — to flag honestly rather than let a `CLEAN` or a correctly-capped
result imply more rigor than the reasoning actually has.
