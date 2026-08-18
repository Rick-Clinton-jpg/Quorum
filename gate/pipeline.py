"""
pipeline.py — Trust Boundary coordinator

Coordinates Sentry, Review-Board, and Reasoning Kernel as three
INDEPENDENT tools, each operating strictly within its own validated
boundary. This module does NOT fuse them into a shared graph and does
NOT enforce cross-tool state. It logs decisions; it does not make them.

See ARCHITECTURE.md for the reasoning behind this design.

NOTE ON INTEGRATION POINTS
---------------------------------------------------------------------
Both integration points below are now confirmed against the real source
of each repo (not README/CLI docs):

  Sentry            confirmed against sentry/__init__.py + sentry/engine.py.
                     scan(text) -> list[Match] (dataclass instances, not
                     dicts — see the fixed _highest_severity() below, which
                     silently produced nothing against real Match objects
                     before this was confirmed).

  Reasoning Kernel   confirmed against kernel.py. There is no
                     Kernel.evaluate(); the real API is a graph/transaction
                     engine (grant/begin/add_node/add_edge/commit()). The
                     SUPPORTED/REFUTED/NEI/INCONCLUSIVE verdict derivation
                     in record_review_board_outcome() below is application
                     logic built on top of that engine — the Kernel itself
                     has no such concept. See the comment above that
                     function for the exact schema and derivation rules,
                     including which parts were confirmed by instruction
                     versus inferred while wiring this up.

  The reasoning-kernel repo has no pyproject.toml/setup.py yet, so it is
  not pip-installable (see requirements.txt and ARCHITECTURE.md). The
  Kernel-side code below is real and wired to the real API, but untested
  end-to-end in this repo until that packaging gap is closed upstream.
---------------------------------------------------------------------
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

try:
    from sentry import scan as sentry_scan_raw  # type: ignore
except ImportError:
    sentry_scan_raw = None

try:
    from kernel import (
        Kernel,
        Node,
        Edge,
        Provenance,
        Confidence,
        NodeType,
        EdgeType,
        Origin,
        Derivation,
        Perm,
        KernelReject,
    )  # type: ignore
except ImportError:
    Kernel = None


class Severity(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


class PipelineAction(str, Enum):
    REJECT = "REJECT"
    PROCEED_WITH_LOG = "PROCEED_WITH_LOG"
    PAUSE_FOR_REVIEW_BOARD = "PAUSE_FOR_REVIEW_BOARD"


@dataclass
class ProvenanceEntry:
    stage: str
    timestamp: str
    detail: dict


@dataclass
class PipelineResult:
    action: PipelineAction
    findings: list = field(default_factory=list)
    provenance: list = field(default_factory=list)
    claim_validation: Optional[dict] = None

    def log(self, stage: str, detail: dict) -> None:
        self.provenance.append(
            ProvenanceEntry(
                stage=stage,
                timestamp=datetime.now(timezone.utc).isoformat(),
                detail=detail,
            )
        )


def _highest_severity(findings: list) -> Severity:
    """Reduce Sentry's findings to its own highest reported severity.
    No new threshold is invented here — Sentry owns this judgment."""
    order = [Severity.HIGH, Severity.MEDIUM, Severity.LOW]
    # findings are sentry.engine.Match instances (frozen dataclasses), not
    # dicts — Match always carries a .severity attribute.
    seen = {f.severity for f in findings}
    for level in order:
        if level.value in seen:
            return level
    return Severity.NONE


def run_input_stage(text: str) -> PipelineResult:
    """Stage 1 — Sentry scans raw input for manipulation patterns."""
    result = PipelineResult(action=PipelineAction.PROCEED_WITH_LOG)

    if sentry_scan_raw is None:
        result.log(
            "sentry_scan",
            {"error": "Sentry not importable — run `pip install -e .` in the Sentry repo"},
        )
        return result

    findings = sentry_scan_raw(text)
    result.findings = findings

    severity = _highest_severity(findings)
    result.log("sentry_scan", {"severity": severity.value, "finding_count": len(findings)})

    result.action = PipelineAction.REJECT if severity == Severity.HIGH else PipelineAction.PROCEED_WITH_LOG
    return result


def pause_for_review_board(result: PipelineResult, reason: str) -> PipelineResult:
    """
    Stage 2 — Human checkpoint.

    This deliberately does NOT invoke Review-Board programmatically.
    Review-Board's own design rejects auto-triggering after adversarial
    testing showed it misfired. A person runs `/review-board` manually
    in Claude Code, then calls record_review_board_outcome() below.
    """
    result.action = PipelineAction.PAUSE_FOR_REVIEW_BOARD
    result.log("pause_for_review_board", {"reason": reason})
    return result


# reasoning-kernel's own eval_harness.py / score_ground_truth.py use 0.60 as
# the "act on this" vs "escalate" threshold on a CONCLUSION node's enforced
# confidence (DECISION_THRESHOLD). That constant lives in eval_harness.py, a
# script, not in the kernel package itself, so it's mirrored here rather than
# imported — keep it in sync with reasoning-kernel if that value ever moves.
DECISION_THRESHOLD = 0.60

# Kernel.grant() takes a module name; the pipeline acts as a single module
# for the purposes of building and committing claim graphs.
_KERNEL_MODULE = "trust_boundary_pipeline"


def _build_claim_graph(kernel: "Kernel", module: str, claim: dict) -> str:
    """
    Build one claim's node/edge graph into `kernel` under an already-open
    transaction. Mirrors reasoning-kernel's own eval_harness._build() — the
    one place in that repo that turns this JSON shape into real Node/Edge
    objects — since that's the only confirmed reference implementation.

    Returns the id of the claim's CONCLUSION node. Raises if the claim is
    malformed (no CONCLUSION node, more than one, or a node/edge the Kernel
    itself rejects at construction time, e.g. a FACT with no confidence).
    """
    conclusion_id = None
    for nd in claim.get("nodes", []):
        prov = None
        if nd.get("origin"):
            prov = Provenance(nd.get("source", "unstated"), Origin(nd["origin"]))
        conf = None
        if nd.get("confidence") is not None:
            conf = Confidence(nd["confidence"], Derivation(nd.get("derivation", "ASSERTED")))

        node_type = NodeType(nd["type"])
        kernel.add_node(module, Node(nd["id"], node_type, nd["label"], provenance=prov, confidence=conf))

        if node_type is NodeType.CONCLUSION:
            if conclusion_id is not None:
                raise ValueError(f"claim {claim.get('id')!r} has more than one CONCLUSION node")
            conclusion_id = nd["id"]

    for src, dst, edge_type in claim.get("edges", []):
        kernel.add_edge(module, Edge(src, dst, EdgeType(edge_type)))

    if conclusion_id is None:
        raise ValueError(f"claim {claim.get('id')!r} has no CONCLUSION node")
    return conclusion_id


def _evaluate_claim(claim: dict) -> dict:
    """
    Run one claim through a fresh Kernel and derive a verdict. See the
    comment above record_review_board_outcome() for the claim schema and
    the full verdict derivation this implements, including which parts
    were confirmed by instruction versus inferred from source.
    """
    kernel = Kernel()
    kernel.grant(_KERNEL_MODULE, {Perm.WRITE})
    kernel.begin(_KERNEL_MODULE)

    try:
        conclusion_id = _build_claim_graph(kernel, _KERNEL_MODULE, claim)
    except Exception as exc:
        # [CONFIRMED — approved by Rick, 2026-08-13, see PR #1 review
        # comment] Not part of the original four-way instruction: a claim
        # that fails to even build (missing/duplicate CONCLUSION, invalid
        # FACT confidence, bad enum value) has no assigned outcome there.
        # Treated as ERROR rather than NEI/REFUTED so a malformed claim
        # doesn't get misread as an evidentiary result.
        kernel.rollback(f"claim build failed: {exc}")
        return {"id": claim.get("id"), "verdict": "ERROR", "detail": str(exc)}

    try:
        kernel.commit()
    except KernelReject as exc:
        rejects = exc.violations
        # CONFIRMED: "If Rule 5 rejects it (no supporting edge) -> treat
        # as NEI, not an error." The `all(v.rule == 5 ...)` condition below
        # — requiring EVERY rejected violation to be rule 5, not just one
        # among others — is an INFERRED refinement on top of that
        # instruction: a claim that fails rule 5 *and* some other rule
        # (e.g. rule 3, "confidence asserted without derivation") is
        # malformed in a way "not enough information" doesn't describe, so
        # it falls through to ERROR below instead of NEI.
        if rejects and all(v.rule == 5 for v in rejects):
            return {
                "id": claim.get("id"),
                "verdict": "NEI",
                "detail": "no supporting/contradicting/inconclusive edge into the conclusion",
            }
        # [CONFIRMED — approved by Rick, 2026-08-13, see PR #1 review
        # comment] Any REJECT not covered by the rule-5 case above (rule
        # 2, 3, 7, or 8) has no assigned outcome in the original four-way
        # instruction; treated as ERROR for the same reason as the
        # build-failure case above.
        return {"id": claim.get("id"), "verdict": "ERROR", "detail": [str(v) for v in rejects]}

    conclusion = kernel.nodes[conclusion_id]
    # INFERRED from kernel.py's _grounding_nodes() (lines ~388-410) — the
    # method Rule 5 itself calls to decide whether a CONCLUSION is grounded
    # at all: grounding is either an inbound SUPPORTS/CONTRADICTS/
    # INCONCLUSIVE edge, OR an outbound DEPENDS_ON edge from the node
    # itself. Only the inbound set is checked below for "only INCONCLUSIVE
    # edges" — an outbound-DEPENDS_ON-only grounding falls through to the
    # confidence threshold instead. Not addressed by instruction either way.
    grounding_types = {
        e.type
        for e in kernel.edges
        if e.dst == conclusion_id and e.type in (EdgeType.SUPPORTS, EdgeType.CONTRADICTS, EdgeType.INCONCLUSIVE)
    }

    # CONFIRMED: "If it commits and has only INCONCLUSIVE-typed edges
    # supporting it -> surface as INCONCLUSIVE."
    if grounding_types == {EdgeType.INCONCLUSIVE}:
        return {"id": claim.get("id"), "verdict": "INCONCLUSIVE"}

    # [CONFIRMED — approved by Rick, 2026-08-13, see PR #1 review comment]
    # Not part of the original four-way instruction. A CONCLUSION node's
    # confidence is legitimately Optional — only FACT nodes require it
    # (kernel.py Node.__post_init__) — so a grounded conclusion with
    # confidence=None is reachable and had no assigned outcome. Named to
    # match score_ground_truth.py's own use of "ABSTAIN" for the identical
    # conf-is-None case (score_ground_truth.py line ~71).
    if conclusion.confidence is None:
        return {"id": claim.get("id"), "verdict": "ABSTAIN"}

    # CONFIRMED: "Otherwise read back confidence and apply
    # DECISION_THRESHOLD -> SUPPORTED/REFUTED, same as eval_harness does."
    # The threshold value and the >= comparison are INFERRED — read
    # directly from reasoning-kernel's score_ground_truth.py line 72
    # (`"SUPPORTED" if conf >= DECISION_THRESHOLD else "REFUTED"`) and
    # eval_harness.py line 62 (`DECISION_THRESHOLD = 0.60`), not restated
    # in the instruction itself.
    verdict = "SUPPORTED" if conclusion.confidence.value >= DECISION_THRESHOLD else "REFUTED"
    return {"id": claim.get("id"), "verdict": verdict, "confidence": conclusion.confidence.value}


def record_review_board_outcome(result: PipelineResult, claims: list) -> PipelineResult:
    """
    Call manually after a human has run Review-Board and it has produced
    specific factual/reasoning claims worth checking.

    CLAIM SCHEMA — confirmed against reasoning-kernel's own eval tooling
    (eval_harness.GraphSpec / ground_truth_cases.py), reused as-is rather
    than inventing a new shape:

        {
          "id": "<claim id>",
          "nodes": [
            {"id": "F0", "type": "FACT", "label": "...",
             "origin": "RETRIEVED"|"USER_INPUT"|"VERIFIED"|"INFERRED"|"REPORTED"|"GENERATED",
             "source": "...", "confidence": 0.0-1.0,
             "derivation": "EVIDENCE"|"REASONING"|"VERIFICATION"},
            ... more FACT/ASSUMPTION/UNKNOWN nodes as needed ...
            {"id": "C0", "type": "CONCLUSION", "label": "...",
             "confidence": 0.0-1.0 (optional), "derivation": "REASONING"}
          ],
          "edges": [["F0", "C0", "SUPPORTS"|"CONTRADICTS"|"INCONCLUSIVE"|"DEPENDS_ON"], ...]
        }

    Each claim must contain exactly one CONCLUSION node — that node's
    post-enforcement state is what gets scored.

    VERDICT DERIVATION — this mapping lives here, in the pipeline, not in
    the Kernel: the Kernel has no SUPPORTED/REFUTED/NEI/INCONCLUSIVE
    concept of its own (confirmed by reading kernel.py). Each line below
    is tagged with exactly where it came from — an instruction given in
    chat (quoted verbatim), or something inferred from reading
    kernel.py / eval_harness.py / score_ground_truth.py (file + line
    cited). Do not treat the two as equivalent: CONFIRMED lines are
    accountable to an exact quote; INFERRED lines are a judgment call
    made while wiring this up and are flagged "needs review" both here
    and at the code site below. ABSTAIN and ERROR were originally
    inferred and flagged "needs review"; both were reviewed and
    explicitly approved by Rick on 2026-08-13 (PR #1 review comment) and
    are now tagged CONFIRMED below — that tag change is provenance only,
    the underlying logic did not change.

      NEI            [CONFIRMED] "If Rule 5 rejects it (no supporting
                      edge) -> treat as NEI, not an error." The exact
                      condition implemented — ALL rejected violations
                      must be rule 5, not just one among others — is an
                      [INFERRED — needs review] refinement on top of
                      that instruction; see the comment above the check
                      in _evaluate_claim().
      INCONCLUSIVE   [CONFIRMED] "If it commits and has only
                      INCONCLUSIVE-typed edges supporting it -> surface
                      as INCONCLUSIVE." Which edge types count as
                      "supporting" (SUPPORTS/CONTRADICTS/INCONCLUSIVE
                      into the conclusion, excluding an outbound
                      DEPENDS_ON) is [INFERRED] from kernel.py's
                      _grounding_nodes() (~lines 388-410), the method
                      Rule 5 itself uses to decide groundedness.
      SUPPORTED /
      REFUTED        [CONFIRMED] "Otherwise read back confidence and
                      apply DECISION_THRESHOLD -> SUPPORTED/REFUTED,
                      same as eval_harness does." The threshold value
                      (0.60) and the >= comparison are [INFERRED] — read
                      directly from score_ground_truth.py line 72 and
                      eval_harness.py line 62, not restated verbatim in
                      the instruction.
      ABSTAIN        [CONFIRMED — approved by Rick, 2026-08-13, see PR #1
                      review comment] Not part of the original four-way
                      instruction. Added because a CONCLUSION's
                      confidence is legitimately Optional — only FACT
                      nodes require it (kernel.py Node.__post_init__) —
                      so a grounded conclusion with no confidence value
                      is reachable and had no assigned outcome. Named to
                      match score_ground_truth.py line ~71's existing use
                      of "ABSTAIN" for the same conf-is-None case.
      ERROR          [CONFIRMED — approved by Rick, 2026-08-13, see PR #1
                      review comment] Not part of the original four-way
                      instruction. Covers two cases that instruction
                      didn't address: (a) a KernelReject where the
                      violations are not rule-5-only (rule 2, 3, 7, or 8),
                      and (b) a claim malformed before commit() is even
                      reached (missing/duplicate CONCLUSION, invalid FACT
                      confidence). Kept distinct from NEI/REFUTED so a
                      broken claim doesn't get silently misread as an
                      evidentiary outcome.

    This validates the specific claims Review-Board made, not whether
    Review-Board's stages were completed in order.
    """
    result.log("review_board_outcome", {"claim_count": len(claims)})

    if Kernel is None:
        result.log("kernel_validate", {"error": "Reasoning Kernel not importable"})
        return result

    verdicts = [_evaluate_claim(claim) for claim in claims]
    result.claim_validation = verdicts
    result.log("kernel_validate", {"verdicts": verdicts})
    return result


def run(text: str) -> PipelineResult:
    """Entry point. Runs Sentry only — everything past that is a human
    decision, not an automated continuation."""
    result = run_input_stage(text)
    if result.action == PipelineAction.REJECT:
        return result
    return pause_for_review_board(
        result, reason="Sentry passed — human decides whether deliberation is warranted"
    )


if __name__ == "__main__":
    import json
    import sys

    input_text = sys.argv[1] if len(sys.argv) > 1 else ""
    outcome = run(input_text)
    print(
        json.dumps(
            {
                "action": outcome.action.value,
                "findings": outcome.findings,
                "provenance": [vars(p) for p in outcome.provenance],
            },
            indent=2,
            default=str,
        )
    )
