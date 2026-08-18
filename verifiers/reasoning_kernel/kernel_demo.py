"""Exercises every kernel rule with a passing and a failing case."""

from kernel import (
    Kernel, Node, Edge, Provenance, Confidence,
    NodeType, EdgeType, State, Perm, Origin, Derivation, KernelReject,
)


def banner(t):
    print(f"\n{'=' * 68}\n{t}\n{'=' * 68}")


k = Kernel()
k.grant("compiler", {Perm.READ, Perm.WRITE})
k.grant("reasoner", {Perm.READ, Perm.WRITE})
k.grant("verifier", {Perm.READ, Perm.WRITE, Perm.VERIFY, Perm.LOCK})
k.grant("janitor", {Perm.READ})          # deliberately no WRITE

banner("A legal graph — 'can I quit my job?'")

k.begin("compiler")
k.add_node("compiler", Node("G1", NodeType.GOAL, "estimate financial risk"))
k.add_node("compiler", Node(
    "F1", NodeType.FACT, "savings = 450000",
    provenance=Provenance("bank statement", Origin.RETRIEVED),
    confidence=Confidence(1.00, Derivation.EVIDENCE, ["bank statement"])))
k.add_node("compiler", Node(
    "A1", NodeType.ASSUMPTION, "monthly expenses stay flat",
    confidence=Confidence(0.42, Derivation.REASONING)))
k.add_node("compiler", Node("U1", NodeType.UNKNOWN, "future income"))
k.add_node("compiler", Node("C1", NodeType.CONSTRAINT, "do not invent values"))
k.add_node("compiler", Node(
    "H1", NodeType.HYPOTHESIS, "runway exceeds 9 months",
    confidence=Confidence(0.40, Derivation.REASONING)))
k.add_edge("compiler", Edge("F1", "H1", EdgeType.SUPPORTS))
k.add_edge("compiler", Edge("A1", "H1", EdgeType.SUPPORTS))
findings = k.commit()
print("committed. findings:")
for f in findings:
    print(f)
print("\ntrace of H1:")
print(k.trace("H1"))


banner("Rule 1 — a FACT the model made up")
k.begin("reasoner")
k.add_node("reasoner", Node(
    "F2", NodeType.FACT, "average rent in Chennai is 18000",
    provenance=Provenance("model parameters", Origin.GENERATED),
    confidence=Confidence(0.90, Derivation.REASONING)))
for f in k.commit():
    print(f)
print(f"\nresult: F2 is now a {k.nodes['F2'].type.value}")
print("        the claim is kept, but it can no longer be cited as fact")


banner("Rule 2 — assigning confidence to an UNKNOWN")
k.begin("reasoner")
k.update_node("reasoner", "U1",
              confidence=Confidence(0.65, Derivation.REASONING))
try:
    k.commit()
except KernelReject as e:
    for v in e.violations:
        print(v)
print(f"\nrolled back. U1 confidence is {k.nodes['U1'].confidence}")


banner("Rule 3a — confidence from nowhere")
k.begin("reasoner")
k.add_node("reasoner", Node(
    "H2", NodeType.HYPOTHESIS, "market will grow",
    confidence=Confidence(0.85, Derivation.ASSERTED)))
try:
    k.commit()
except KernelReject as e:
    for v in e.violations:
        print(v)
print(f"\nrolled back. H2 present: {'H2' in k.nodes}")


banner("Rule 3b — a confident conclusion resting on a weak assumption")
k.begin("reasoner")
k.add_node("reasoner", Node(
    "CN1", NodeType.CONCLUSION, "quitting is financially safe",
    confidence=Confidence(0.95, Derivation.REASONING)))
k.add_edge("reasoner", Edge("A1", "CN1", EdgeType.SUPPORTS))
k.add_edge("reasoner", Edge("H1", "CN1", EdgeType.SUPPORTS))
for f in k.commit():
    print(f)
weakest = min(k.nodes[s].confidence.value for s in k._support_nodes("CN1"))
print(f"\nconfidence capped to {k.nodes['CN1'].confidence.value:.2f} "
      f"— the weakest support was {weakest:.2f}")
print("\ntrace of CN1:")
print(k.trace("CN1"))


banner("Rule 4 — stripping provenance from a verified fact")
k.begin("verifier")
try:
    k.update_node("verifier", "F1", provenance=None)
except KernelReject as e:
    for v in e.violations:
        print(v)
k.rollback("attempted provenance strip")
print(f"\nF1 provenance intact: {k.nodes['F1'].provenance.source}")


banner("Rule 5 — a conclusion with nothing under it")
k.begin("reasoner")
k.add_node("reasoner", Node(
    "CN2", NodeType.CONCLUSION, "therefore, expand to Bangalore",
    confidence=Confidence(0.50, Derivation.REASONING)))
try:
    k.commit()
except KernelReject as e:
    for v in e.violations:
        print(v)


banner("Rule 6 — contradictions are preserved, never deleted")
k.begin("reasoner")
k.add_node("reasoner", Node(
    "F3", NodeType.FACT, "expenses rose 12% last quarter",
    provenance=Provenance("expense log", Origin.RETRIEVED),
    confidence=Confidence(0.95, Derivation.EVIDENCE)))
k.add_edge("reasoner", Edge("F3", "A1", EdgeType.CONTRADICTS))
for f in k.commit():
    print(f)
print(f"\nA1 contested: {k.nodes['A1'].contested}   "
      f"F3 contested: {k.nodes['F3'].contested}")

print("\nnow try to delete the inconvenient one:")
k.begin("verifier")
k.grant("verifier", {Perm.READ, Perm.WRITE, Perm.VERIFY, Perm.LOCK, Perm.DELETE})
try:
    k.delete_node("verifier", "A1")
except KernelReject as e:
    for v in e.violations:
        print(v)
k.rollback("deletion refused")


banner("Rule 7 — node identity is immutable")
k.begin("reasoner")
try:
    k.update_node("reasoner", "F1", id="F99")
except KernelReject as e:
    for v in e.violations:
        print(v)
k.rollback("id change refused")
print("\nrelabelling is fine — identity is the id, not the wording:")
k.begin("reasoner")
k.update_node("reasoner", "F1", label="savings balance = 450000 INR")
k.commit()
print(f"F1 is still F1, now labelled: {k.nodes['F1'].label!r}")


banner("Rule 8 — no silent mutation, and no circular reasoning")
k.begin("reasoner")
k.add_node("reasoner", Node("X1", NodeType.HYPOTHESIS, "demand is high",
                            confidence=Confidence(0.5, Derivation.REASONING)))
k.add_node("reasoner", Node("X2", NodeType.HYPOTHESIS, "prices are rising",
                            confidence=Confidence(0.5, Derivation.REASONING)))
k.add_edge("reasoner", Edge("X1", "X2", EdgeType.DEPENDS_ON))
k.add_edge("reasoner", Edge("X2", "X1", EdgeType.DEPENDS_ON))
try:
    k.commit()
except KernelReject as e:
    for v in e.violations:
        print(v)


banner("Permissions — a module without WRITE")
try:
    k.begin("janitor")
    k.add_node("janitor", Node(
        "Z1", NodeType.FACT, "anything",
        provenance=Provenance("irrelevant here", Origin.RETRIEVED),
        confidence=Confidence(0.50, Derivation.EVIDENCE)))
except PermissionError as e:
    print(f"  PermissionError: {e}")
k.rollback("permission denied")


banner("Event log — every change, in order")
for e in k.events:
    print(e)
print(f"\n{len(k.events)} events. Nothing changed without one.")
