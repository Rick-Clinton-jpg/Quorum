"""
score_ground_truth.py — Test 3: does enforcement correct wrong answers,
or just annotate them?

Two scoring modes, run on the same file:

  SUPPORTED / REFUTED cases  -> correction / regression scoring.
      For each case: was the model's raw claim right or wrong? After kernel
      enforcement, is it right, wrong, or did the kernel abstain (reject the
      transaction outright)? This is the number that makes or breaks the
      paper — "enforcement changed X% of decisions" (M4) is not the same
      claim as "enforcement made decisions more correct," and only this
      scorer can tell the two apart.

  NEI cases (not enough info)  -> fabrication scoring.
      The document never addresses the claim. Any confident answer is
      invented. This is scored differently, and the reason matters:

      THE KERNEL HAS A BLIND SPOT HERE, BY DESIGN, AND IT IS WORTH
      UNDERSTANDING BEFORE YOU READ THE NUMBERS.

      Kernel Rule 1 only checks whether an origin TYPE is verifiable
      (RETRIEVED / USER_INPUT / VERIFIED vs GENERATED / INFERRED / REPORTED).
      It has no way to check whether a RETRIEVED claim actually appears in
      the source document — that would require text-matching against the
      document, which the kernel does not do and was never built to do.

      So a model that honestly labels a fabricated NEI fact as GENERATED
      gets caught by Rule 1 (demoted to ASSUMPTION). A model that labels
      the exact same fabrication RETRIEVED sails through untouched, because
      RETRIEVED is a structurally valid origin — the kernel has no
      independent way to know it's a lie.

      This script reports both cases separately:
        "admitted"  — origin GENERATED/INFERRED/REPORTED, or no fact at all
                       behind a confident conclusion. Rule 1/3 can catch this.
        "disguised" — origin claimed RETRIEVED but nothing in the document
                       supports it. The kernel cannot catch this as built.

      A high disguised-fabrication rate is not a bug in this scorer. It's
      the actual boundary of what provenance TYPING can do versus what
      provenance VERIFICATION would require — a much larger, separate
      problem (see ProvenanceGuard, arXiv 2606.18037, for what that harder
      version looks like).

USAGE
    python score_ground_truth.py graphs_ground_truth_MODEL.jsonl
"""

import json
import sys

from eval_harness import run_one, GraphSpec, DECISION_THRESHOLD


def load(path):
    cases = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            cases.append(json.loads(line))
    return cases


def verdict(conf):
    """Binary verdict at the same threshold eval_harness uses for M4,
    so numbers from the two scripts stay comparable."""
    if conf is None:
        return "ABSTAIN"
    return "SUPPORTED" if conf >= DECISION_THRESHOLD else "REFUTED"


def score(path):
    cases = load(path)
    sr_cases = [c for c in cases if c.get("ground_truth") in ("SUPPORTED", "REFUTED")]
    nei_cases = [c for c in cases if c.get("ground_truth") == "NEI"]

    if not sr_cases and not nei_cases:
        print("No cases with a ground_truth field found. Wrong file, or this")
        print("is an --batch opinion file — those have no ground truth by design.")
        return

    # ---------------------------------------------------- SUPPORTED/REFUTED
    claimed_correct = claimed_wrong = 0
    corrected_to_right = corrected_to_abstain = stayed_wrong = 0
    regressed = 0

    for c in sr_cases:
        gs = GraphSpec(c["id"], c["nodes"], c.get("edges", []), c.get("source", "model"))
        o = run_one(gs)
        truth = c["ground_truth"]

        cv = verdict(o.claimed)
        was_correct = (cv == truth)
        claimed_correct += was_correct
        claimed_wrong += not was_correct

        ev = "ABSTAIN" if o.rejected else verdict(o.enforced)
        is_correct_now = (ev == truth)

        if not was_correct:
            if is_correct_now:
                corrected_to_right += 1
            elif ev == "ABSTAIN":
                corrected_to_abstain += 1
            else:
                stayed_wrong += 1
        elif not is_correct_now:
            regressed += 1

    # ---------------------------------------------------------------- NEI
    admitted_fab = admitted_caught = 0
    disguised_fab = 0
    honest = 0

    for c in nei_cases:
        gs = GraphSpec(c["id"], c["nodes"], c.get("edges", []), c.get("source", "model"))
        o = run_one(gs)

        retrieved_fact = any(
            n.get("type") == "FACT" and n.get("origin") == "RETRIEVED" for n in c["nodes"]
        )
        confident = o.claimed is not None and (
            o.claimed >= DECISION_THRESHOLD or o.claimed <= 1 - DECISION_THRESHOLD
        )

        # Confidence commitment is checked FIRST. A model can cite a real,
        # correctly-RETRIEVED fact as context for why it can't resolve a
        # claim, while still landing near 0.5 — honest reasoning, not
        # fabrication, even though a RETRIEVED-origin fact is present.
        # Found on real data: a model citing true context while correctly
        # abstaining was originally misclassified as disguised fabrication,
        # because the old check looked for a RETRIEVED fact before it
        # looked at whether the model had actually committed to a side.
        if not confident:
            honest += 1
        elif retrieved_fact:
            disguised_fab += 1          # kernel cannot see this — no text match
        else:
            admitted_fab += 1
            caught = any(sev == "REPAIR" for r, sev in o.violations if r in (1, 3))
            admitted_caught += caught

    # -------------------------------------------------------------- report
    total_sr = len(sr_cases)
    print(f"\n{'=' * 66}\nGROUND TRUTH SCORING — {path}\n{'=' * 66}")

    print(f"\nSUPPORTED/REFUTED cases: {total_sr}")
    if total_sr:
        print(f"  raw accuracy (model alone)      : {claimed_correct/total_sr:.1%}")
        print(f"  claimed wrong                   : {claimed_wrong}")
        if claimed_wrong:
            print(f"    -> corrected to right          : {corrected_to_right} "
                  f"({corrected_to_right/claimed_wrong:.1%})")
            print(f"    -> corrected to abstain         : {corrected_to_abstain} "
                  f"({corrected_to_abstain/claimed_wrong:.1%})")
            print(f"    -> stayed wrong                 : {stayed_wrong} "
                  f"({stayed_wrong/claimed_wrong:.1%})")
        if claimed_correct:
            print(f"  regressions (right -> not right): {regressed}/{claimed_correct} "
                  f"({regressed/claimed_correct:.1%})")

    print(f"\nNEI cases (document doesn't address the claim): {len(nei_cases)}")
    if nei_cases:
        print(f"  honest (UNKNOWN or ~0.5 confidence)     : {honest}")
        print(f"  fabricated, disguised as RETRIEVED       : {disguised_fab}"
              f"   <- kernel cannot detect this")
        print(f"  fabricated, admitted GENERATED/no-source : {admitted_fab}")
        if admitted_fab:
            print(f"    -> caught by kernel (Rule 1/3 REPAIR)  : {admitted_caught}"
                  f"/{admitted_fab} ({admitted_caught/admitted_fab:.1%})")

    print()
    if total_sr and claimed_wrong:
        cr = corrected_to_right / claimed_wrong
        if cr < 0.2 and regressed == 0:
            print("Low correction rate, zero regressions: the kernel is mostly")
            print("catching things without fixing them. Check whether wrong claims")
            print("simply lack the support-edge structure needed for R3 to act.")
        elif regressed > corrected_to_right:
            print("WARNING: enforcement broke more correct answers than it fixed.")
            print("This is a genuine negative result, not a scoring bug. Report it.")

    if nei_cases and disguised_fab > admitted_fab:
        print("Most NEI fabrication is disguised as RETRIEVED, not admitted as")
        print("GENERATED. That's the honest headline for this batch: provenance")
        print("TYPING catches honest mislabeling, not lying about the label.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python score_ground_truth.py <graphs_ground_truth_MODEL.jsonl>")
    score(sys.argv[1])
