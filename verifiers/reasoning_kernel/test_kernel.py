"""
Tests for the Rule 3 confidence-ceiling bug: a FACT node with
confidence=None used to drop silently out of Kernel._cap's aggregation,
capping every downstream CONCLUSION/HYPOTHESIS at 0.0 regardless of what
the graph actually supported. Fixed with two layers:

  1. Node.__post_init__ makes it impossible to construct a FACT node
     without a valid (non-None, 0.0-1.0) confidence.
  2. Kernel._cap raises loudly if it ever encounters a FACT support node
     with confidence=None, instead of silently excluding it.

Standard library only, matching the rest of this repo.
"""

import unittest

from kernel import (
    Kernel, Node, Edge, Provenance, Confidence,
    NodeType, EdgeType, Perm, Origin, Derivation, FactConfidenceError,
    KernelReject,
)


class FactNodeConstructionTests(unittest.TestCase):

    def test_fact_without_confidence_raises(self):
        with self.assertRaises(FactConfidenceError) as ctx:
            Node("F1", NodeType.FACT, "savings = 450000",
                 provenance=Provenance("bank statement", Origin.RETRIEVED))
        msg = str(ctx.exception)
        self.assertIn("F1", msg)
        self.assertIn("without confidence", msg)
        # error must point at where to source the value, not just say "invalid"
        self.assertIn("source", msg)

    def test_fact_with_out_of_range_confidence_raises(self):
        for bad in (-0.1, 1.5):
            with self.subTest(value=bad):
                with self.assertRaises(FactConfidenceError) as ctx:
                    Node("F1", NodeType.FACT, "x",
                         confidence=Confidence(bad, Derivation.EVIDENCE))
                self.assertIn("0.0-1.0", str(ctx.exception))

    def test_fact_with_valid_confidence_succeeds_unchanged(self):
        n = Node("F1", NodeType.FACT, "savings = 450000",
                 provenance=Provenance("bank statement", Origin.RETRIEVED),
                 confidence=Confidence(0.90, Derivation.EVIDENCE, ["bank statement"]))
        self.assertEqual(n.id, "F1")
        self.assertEqual(n.type, NodeType.FACT)
        self.assertEqual(n.confidence.value, 0.90)
        self.assertEqual(n.confidence.derivation, Derivation.EVIDENCE)

    def test_non_fact_nodes_unaffected(self):
        # ASSUMPTION and UNKNOWN may legitimately have no confidence;
        # the new validation must not touch other node types.
        Node("A1", NodeType.ASSUMPTION, "expenses stay flat")
        Node("U1", NodeType.UNKNOWN, "future income")
        Node("C1", NodeType.CONCLUSION, "quitting is safe")


class Rule3AggregationTests(unittest.TestCase):

    def _kernel(self):
        k = Kernel()
        k.grant("m", {Perm.READ, Perm.WRITE})
        return k

    def test_valid_graph_produces_correct_nonzero_conclusion(self):
        k = self._kernel()
        k.begin("m")
        k.add_node("m", Node(
            "F1", NodeType.FACT, "savings = 450000",
            provenance=Provenance("bank statement", Origin.RETRIEVED),
            confidence=Confidence(0.90, Derivation.EVIDENCE)))
        k.add_node("m", Node(
            "F2", NodeType.FACT, "expenses = 20000/mo",
            provenance=Provenance("expense log", Origin.RETRIEVED),
            confidence=Confidence(0.70, Derivation.EVIDENCE)))
        k.add_node("m", Node(
            "CN1", NodeType.CONCLUSION, "runway exceeds 12 months",
            confidence=Confidence(0.99, Derivation.REASONING)))
        k.add_edge("m", Edge("F1", "CN1", EdgeType.SUPPORTS))
        k.add_edge("m", Edge("F2", "CN1", EdgeType.SUPPORTS))
        k.commit()

        conf = k.nodes["CN1"].confidence.value
        # capped to the weakest support (min propagation), NOT to 0.0
        self.assertAlmostEqual(conf, 0.70)
        self.assertGreater(conf, 0.0)

    def test_cap_raises_on_bypassed_none_confidence_fact(self):
        """Regression test for the original bug scenario.

        Node.__post_init__ blocks constructing a FACT with confidence=None
        outright, so to exercise the *aggregation* defense-in-depth layer
        we simulate a bypass (e.g. a mutated attribute, or a graph
        reconstructed without going through Node()) by clearing confidence
        on an already-built FACT node directly.

        Before the fix, this silently capped CN1 at 0.0. Now it must raise
        instead of ever producing that silent zero.
        """
        k = self._kernel()
        k.begin("m")
        k.add_node("m", Node(
            "F1", NodeType.FACT, "savings = 450000",
            provenance=Provenance("bank statement", Origin.RETRIEVED),
            confidence=Confidence(0.90, Derivation.EVIDENCE)))
        k.add_node("m", Node(
            "CN1", NodeType.CONCLUSION, "runway exceeds 12 months",
            confidence=Confidence(0.85, Derivation.REASONING)))
        k.add_edge("m", Edge("F1", "CN1", EdgeType.SUPPORTS))

        # simulate a validation bypass — direct mutation past the constructor
        k.nodes["F1"].confidence = None

        with self.assertRaises(FactConfidenceError) as ctx:
            k.commit()
        msg = str(ctx.exception)
        self.assertIn("CN1", msg)
        self.assertIn("F1", msg)
        # must NOT have silently capped CN1 to 0.0 — the commit never
        # completed, so CN1's confidence is untouched from what was set
        self.assertAlmostEqual(k.nodes["CN1"].confidence.value, 0.85)

    def test_original_bug_scenario_errors_instead_of_zeroing(self):
        """End-to-end: a FACT that would have entered the graph with
        confidence=None (e.g. from a model emitting `"confidence": null`
        for a FACT node, the real-world path in eval_harness._build) can
        no longer be constructed at all — the graph build fails loudly
        instead of silently producing a CONCLUSION capped at 0.0."""
        with self.assertRaises(FactConfidenceError):
            Node("F1", NodeType.FACT, "average rent in Chennai is 18000",
                 provenance=Provenance("doc", Origin.RETRIEVED),
                 confidence=None)

    def test_assumption_without_confidence_still_excluded_not_raised(self):
        """Defense-in-depth is scoped to FACT nodes. An ASSUMPTION with no
        confidence is a pre-existing, intentional WARN-only state (Rule 3)
        and must keep being excluded from the ceiling calc, not raise."""
        k = self._kernel()
        k.begin("m")
        k.add_node("m", Node("A1", NodeType.ASSUMPTION, "expenses stay flat"))
        k.add_node("m", Node(
            "F1", NodeType.FACT, "savings = 450000",
            provenance=Provenance("bank statement", Origin.RETRIEVED),
            confidence=Confidence(0.80, Derivation.EVIDENCE)))
        k.add_node("m", Node(
            "CN1", NodeType.CONCLUSION, "quitting is safe",
            confidence=Confidence(0.99, Derivation.REASONING)))
        k.add_edge("m", Edge("A1", "CN1", EdgeType.SUPPORTS))
        k.add_edge("m", Edge("F1", "CN1", EdgeType.SUPPORTS))
        findings = k.commit()  # must not raise
        self.assertAlmostEqual(k.nodes["CN1"].confidence.value, 0.80)


class Rule8SupportCycleTests(unittest.TestCase):
    """Rule 8's cycle check used to look only at DEPENDS_ON edges, so a
    circular SUPPORTS chain (A supports B, B supports A — each node citing
    the other as its own justification) passed validation with zero
    findings: Rule 5's traceability check only requires a non-empty
    grounding list, which a cycle trivially satisfies. _has_cycle now walks
    the same 'what does X rest on' relation _support_nodes computes for
    Rule 3 (DEPENDS_ON: src rests on dst; SUPPORTS: dst rests on src), so a
    cycle in either edge type — or a mix of both — is caught."""

    def _kernel(self):
        k = Kernel()
        k.grant("m", {Perm.READ, Perm.WRITE})
        return k

    def test_two_node_supports_cycle_rejected(self):
        k = self._kernel()
        k.begin("m")
        k.add_node("m", Node("A", NodeType.CONCLUSION, "A",
                              confidence=Confidence(0.5, Derivation.REASONING)))
        k.add_node("m", Node("B", NodeType.CONCLUSION, "B",
                              confidence=Confidence(0.5, Derivation.REASONING)))
        k.add_edge("m", Edge("A", "B", EdgeType.SUPPORTS))
        k.add_edge("m", Edge("B", "A", EdgeType.SUPPORTS))
        with self.assertRaises(KernelReject) as ctx:
            k.commit()
        self.assertTrue(any(v.rule == 8 for v in ctx.exception.violations))

    def test_self_supporting_node_rejected(self):
        k = self._kernel()
        k.begin("m")
        k.add_node("m", Node("A", NodeType.CONCLUSION, "A",
                              confidence=Confidence(0.5, Derivation.REASONING)))
        k.add_edge("m", Edge("A", "A", EdgeType.SUPPORTS))
        with self.assertRaises(KernelReject) as ctx:
            k.commit()
        self.assertTrue(any(v.rule == 8 for v in ctx.exception.violations))

    def test_mixed_supports_depends_on_cycle_rejected(self):
        k = self._kernel()
        k.begin("m")
        k.add_node("m", Node("A", NodeType.CONCLUSION, "A",
                              confidence=Confidence(0.5, Derivation.REASONING)))
        k.add_node("m", Node("B", NodeType.CONCLUSION, "B",
                              confidence=Confidence(0.5, Derivation.REASONING)))
        # A rests on B via SUPPORTS (B -> A), B rests on A via DEPENDS_ON (B -> A)
        k.add_edge("m", Edge("B", "A", EdgeType.SUPPORTS))
        k.add_edge("m", Edge("B", "A", EdgeType.DEPENDS_ON))
        with self.assertRaises(KernelReject) as ctx:
            k.commit()
        self.assertTrue(any(v.rule == 8 for v in ctx.exception.violations))

    def test_convergent_supports_not_flagged_as_cycle(self):
        """A diamond — two independent facts supporting the same hypothesis,
        which supports a conclusion — is a legitimate DAG, not a cycle, and
        must still commit cleanly."""
        k = self._kernel()
        k.begin("m")
        k.add_node("m", Node("F1", NodeType.FACT, "f1",
                              provenance=Provenance("s1", Origin.RETRIEVED),
                              confidence=Confidence(0.8, Derivation.EVIDENCE)))
        k.add_node("m", Node("F2", NodeType.FACT, "f2",
                              provenance=Provenance("s2", Origin.RETRIEVED),
                              confidence=Confidence(0.9, Derivation.EVIDENCE)))
        k.add_node("m", Node("H1", NodeType.HYPOTHESIS, "h1",
                              confidence=Confidence(0.7, Derivation.REASONING)))
        k.add_node("m", Node("C1", NodeType.CONCLUSION, "c1",
                              confidence=Confidence(0.7, Derivation.REASONING)))
        k.add_edge("m", Edge("F1", "H1", EdgeType.SUPPORTS))
        k.add_edge("m", Edge("F2", "H1", EdgeType.SUPPORTS))
        k.add_edge("m", Edge("H1", "C1", EdgeType.SUPPORTS))
        findings = k.commit()  # must not raise
        self.assertFalse(any(v.rule == 8 for v in findings))

    def test_depends_on_cycle_still_rejected(self):
        """Pre-existing DEPENDS_ON-only cycle detection must keep working."""
        k = self._kernel()
        k.begin("m")
        k.add_node("m", Node("X1", NodeType.CONCLUSION, "x1"))
        k.add_node("m", Node("X2", NodeType.CONCLUSION, "x2"))
        k.add_edge("m", Edge("X1", "X2", EdgeType.DEPENDS_ON))
        k.add_edge("m", Edge("X2", "X1", EdgeType.DEPENDS_ON))
        with self.assertRaises(KernelReject) as ctx:
            k.commit()
        self.assertTrue(any(v.rule == 8 for v in ctx.exception.violations))


if __name__ == "__main__":
    unittest.main()
