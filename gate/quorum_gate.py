"""Quorum's gate coordinator - Phase 3.

Wires a Worker Agent Proposal (worker_agent/schema.py's Proposal, as
dict) through:

  - Sentry, as a verifier of the Worker Agent's OWN output (its diff +
    rationale text) - distinct from Sentry as the Worker Agent's *target*
    repo in Phase 2. Do not conflate the two roles; see
    docs/INTEGRATION_MAP.md's explicit warning about this.
  - Reasoning Kernel, to check provenance/logic integrity of the claims
    the rationale depends on.
  - IntentGraph, to catch a task description that's really a
    reformulated return to an objective this gate already rejected.
  - Warden, for an append-only audit log only - never its drift-tag
    matcher (docs/INTEGRATION_MAP.md Section 6b: that detector is
    confirmed inverted, AUC 0.332, and this project does not gate on it).

This module is NEW Quorum-specific glue. It reuses gate/pipeline.py's
existing, tested functions (run_input_stage, pause_for_review_board,
record_review_board_outcome) as-is rather than reimplementing them -
gate/CONTRIBUTING.md explicitly reserves that logic for the repo owner to
change, and "coordinate, don't fuse" (gate/ARCHITECTURE.md) is the whole
point of Trust Boundary's design in the first place.

Verdicts:
    PASS      - Sentry clean, Kernel says SUPPORTED, IntentGraph risk < HIGH.
    REJECT    - Sentry HIGH finding, or Kernel says REFUTED/ERROR. Retryable:
                retry_gate() feeds the reason back to the Worker Agent for
                one redraft.
    ESCALATE  - IntentGraph flags HIGH re-entry risk, or the Kernel's claim
                graph didn't clearly resolve (NEI/INCONCLUSIVE/ABSTAIN).
                NOT retried automatically - this is exactly the case
                Review-Board exists for, and Review-Board has zero
                programmatic API (docs/INTEGRATION_MAP.md Section 4): it's
                a human running /review-board, not code Quorum can call.
                Reuses pipeline.py's own pause_for_review_board() for this.

A REJECT marks a safety-boundary turn in the IntentGraph session
afterward, so a later reformulated resubmission of the same rejected
objective is visible to re-entry detection. ESCALATE does not - it isn't
a confirmed-bad outcome, just an undecided one, and marking it would
poison the intent graph against something that was never actually
rejected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from . import quorum_paths  # noqa: F401 - import first, for its sys.path side effects
from .otel_tracing import stage_span
from .redaction import scrub_pii

import pipeline  # gate/pipeline.py - flat import, see quorum_paths.py
from pipeline import PipelineAction, PipelineResult

from intent_layer import IntentGraph, score_node  # verifiers/intent_graph
from warden.audit import AuditLogger  # verifiers/warden - log-only, never warden.matcher

REPO_ROOT = quorum_paths.REPO_ROOT
DEFAULT_AUDIT_ROOT = REPO_ROOT / ".quorum"


def _added_lines(diff: str) -> str:
    """Extracts only the lines a unified diff adds (`+...`, excluding the
    `+++` file header), dropping unchanged context and removed lines.

    Scanning the *whole* diff string was tried first and produces a real
    false positive on exactly the target repo this project cares about:
    a patch to Sentry's own rules/tests file necessarily carries unchanged
    context lines that already look like exfiltration patterns (the
    existing network_exfil_pattern rule's own regex text, an existing
    test's `os.environ` string), which then trips Sentry's own
    env_exfil_pattern/network_exfil_pattern rules on content nobody is
    proposing to add. Unchanged/removed lines were already accepted in a
    prior commit; what the gate needs to check is what's new.

    Excludes `+++ ` (with the trailing space) specifically, not any line
    starting with `++` - a real unified-diff file header always has that
    space before the path (`+++ b/some/file`). The looser check excluded
    a genuinely ADDED line from the safety scan whenever its own content
    happened to start with `++` (e.g. an added source line like
    `++counter;`), which is exactly the content this function exists to
    scan, not skip.
    """
    return "\n".join(
        line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++ ")
    )


# Confirmed live in this project's own level-1-10 adversarial test run
# (docs/LEVEL_TEST_FINDINGS.md, Level 8): a trigger phrase split across a
# sentence boundary - e.g. "no conf. irmation needed" - evades every
# Sentry rule, since each rule matches a single contiguous span and no
# span in the original text ever spells the phrase out whole.
_SENTENCE_BREAK_RE = re.compile(r"[.!?]\s+")


def _defragmented(text: str) -> str:
    """Collapses sentence-terminal punctuation + the whitespace after it,
    so a phrase split across exactly one such boundary re-joins into a
    single contiguous span Sentry's regexes can actually match. Scanned
    IN ADDITION to the original text, never instead of it - this is a
    narrow, disclosed heuristic for one specific evasion shape, not a
    general defense against arbitrary fragmentation, and deliberately
    lives here rather than in verifiers/sentry/ itself: modifying a
    vendored verifier's own matching semantics is exactly what
    "coordinate, don't fuse" (gate/ARCHITECTURE.md) says this project
    doesn't do.
    """
    return _SENTENCE_BREAK_RE.sub("", text)


# Verdicts the Kernel's own claim-graph evaluation can return (see
# gate/pipeline.py's record_review_board_outcome docstring - this project
# does not invent new ones).
_ESCALATE_KERNEL_VERDICTS = {"NEI", "INCONCLUSIVE", "ABSTAIN"}
_REJECT_KERNEL_VERDICTS = {"REFUTED", "ERROR"}


class GateVerdict(str, Enum):
    PASS = "PASS"
    REJECT = "REJECT"
    ESCALATE = "ESCALATE"


@dataclass
class GateResult:
    verdict: GateVerdict
    reasons: list[str] = field(default_factory=list)
    sentry_action: Optional[str] = None
    kernel_verdict: Optional[dict] = None
    intent_risk: Optional[str] = None
    agent_self_report: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Kernel claim-graph translation
# ---------------------------------------------------------------------------
# A claim's "source" earns VERIFIED only if it resolves to a real file
# under one of these roots AND the gate re-reads that file and finds the
# statement's own distinctive content in it. Everything else - an
# external citation, a vague description, a path outside these roots, or
# a resolvable path whose content doesn't actually back the statement -
# is REPORTED, no matter what the agent's own `origin` field claimed.
_VERIFIABLE_ROOTS = [REPO_ROOT / "verifiers", REPO_ROOT / "gate"]

# Where a bare relative path/token found inside a claim's "source" string
# might actually live - tried in order. Handles both a clean path
# ("rules/default_rules.json") and one a repo-relative prefix would be
# needed for, without trusting the agent to have written either form.
_SEARCH_ROOTS = [
    REPO_ROOT,
    REPO_ROOT / "verifiers",
    REPO_ROOT / "verifiers" / "sentry",
    REPO_ROOT / "verifiers" / "reasoning_kernel",
    REPO_ROOT / "verifiers" / "review_board",
    REPO_ROOT / "verifiers" / "intent_graph",
    REPO_ROOT / "verifiers" / "warden",
    REPO_ROOT / "gate",
]

_PATH_TOKEN_RE = re.compile(r"[\w][\w./-]*\.[A-Za-z0-9]{1,8}")
_QUOTE_RE = re.compile(r"[`'\"]([^`'\"]{3,80})[`'\"]")
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{4,}")
_STOPWORDS = {
    "which", "there", "their", "about", "these", "those", "would", "could",
    "should", "where", "because", "since", "while", "against", "current",
}
_CONTENT_OVERLAP_THRESHOLD = 0.6


def _resolve_claim_source_path(source: str) -> Optional[Path]:
    """Returns the real file a claim's `source` string points at, if the
    gate can find one under a root it's willing to re-check - else None.

    Deliberately strict: a source has to contain something path-shaped
    that actually resolves to a file under _VERIFIABLE_ROOTS. A citation
    to external literature, a vague description with no path in it, or a
    path outside the vendored verifiers never resolves here.
    """
    tokens = _PATH_TOKEN_RE.findall(source) or [source]
    for token in tokens:
        for root in _SEARCH_ROOTS:
            candidate = (root / token).resolve()
            if not candidate.is_file():
                continue
            for allowed in _VERIFIABLE_ROOTS:
                allowed = allowed.resolve()
                if candidate == allowed or allowed in candidate.parents:
                    return candidate
    return None


# A small, deliberately conservative negation word list - not full NLP
# negation-scope detection. Known false-positive traps: "not uncommon",
# "not only", double negation. That's the safe direction to be wrong in:
# this function never returns "REFUTED" (that verdict, when it happens,
# comes from the separately vendored Reasoning Kernel, not from here) -
# a false hit here only downgrades a possibly-genuine VERIFIED to
# REPORTED, never the reverse. Confirmed as a real gap without this: a
# claim's overlapping words with its source could reach the ratio
# threshold below even when the statement asserts the opposite of what
# the source actually says (the source negates the shared terms, the
# statement doesn't, or vice versa) - "unsupported claims marked
# VERIFIED via lexical overlap alone."
_NEGATION_WORDS = {"not", "never", "cannot", "can't", "won't", "isn't", "doesn't", "wasn't", "aren't", "no longer"}


def _has_negation(text: str) -> bool:
    lowered = text.lower()
    return any(w in lowered for w in _NEGATION_WORDS)


def _source_lines_with_any_term(words: set[str], content: str) -> str:
    """Only the lines of `content` containing at least one of the
    claim's own distinctive terms - scopes the negation check to where
    the claim's actual evidence is, not the whole file, which would
    almost always contain SOME negation word unrelated to this specific
    claim."""
    return "\n".join(line for line in content.splitlines() if any(w in line.lower() for w in words))


def determine_claim_origin(claim: dict) -> tuple[str, str]:
    """Gate-determined origin for one claim - "VERIFIED" or "REPORTED".

    NEVER returns claim["origin"] - that field is the agent's own
    self-report and is not trusted here (see this module's docstring and
    the Phase 3 addendum). Returns (origin, note); note explains the
    decision, for the audit log.
    """
    path = _resolve_claim_source_path(claim["source"])
    if path is None:
        return "REPORTED", f"source {claim['source']!r} does not resolve to a file under {_VERIFIABLE_ROOTS}"

    content = path.read_text(errors="ignore")

    quoted = _QUOTE_RE.findall(claim["statement"])
    if quoted:
        if all(q in content for q in quoted):
            return "VERIFIED", f"every quoted span in the statement appears verbatim in {path.name}"
        return "REPORTED", f"a quoted span in the statement was not found verbatim in {path.name}"

    words = {w.lower() for w in _WORD_RE.findall(claim["statement"]) if w.lower() not in _STOPWORDS}
    if not words:
        return "REPORTED", "statement has no distinctive terms to re-check against source content"
    hay = content.lower()
    hits = sum(1 for w in words if w in hay)
    ratio = hits / len(words)
    if ratio >= _CONTENT_OVERLAP_THRESHOLD:
        relevant_source = _source_lines_with_any_term(words, content)
        if _has_negation(claim["statement"]) != _has_negation(relevant_source):
            return (
                "REPORTED",
                f"{hits}/{len(words)} distinctive terms matched {path.name}, but the statement's negation "
                "doesn't match the source lines containing those terms - not confirmed as written",
            )
        return "VERIFIED", f"{hits}/{len(words)} distinctive statement terms found in {path.name}"
    return "REPORTED", f"only {hits}/{len(words)} distinctive statement terms found in {path.name} - not enough to confirm"


def build_claim_graph(proposal: dict) -> tuple[dict, list[dict]]:
    """Translates proposal["claims"] (flat Worker Agent claims) into the
    single-claim-graph shape gate/pipeline.py's record_review_board_outcome()
    already expects (see its docstring): one FACT node per claim, one
    CONCLUSION node standing for "the rationale is supported by its
    claims", SUPPORTS edges from every FACT into that CONCLUSION.

    Returns (claim_graph, agent_self_report). agent_self_report preserves
    the agent's own origin/confidence per claim, unmodified, alongside
    what the gate independently decided - for Warden to log, never for
    the Kernel to see.
    """
    nodes: list[dict] = []
    edges: list[list[str]] = []
    agent_self_report: list[dict] = []

    for claim in proposal["claims"]:
        gate_origin, note = determine_claim_origin(claim)
        agent_self_report.append(
            {
                "id": claim["id"],
                "agent_origin": claim["origin"],
                "agent_confidence": claim["confidence"],
                "gate_origin": gate_origin,
                "gate_note": note,
            }
        )
        nodes.append(
            {
                "id": claim["id"],
                "type": "FACT",
                "label": claim["statement"],
                "origin": gate_origin,
                "source": claim["source"],
                "confidence": claim["confidence"],
                # REASONING, not ASSERTED: ASSERTED is illegal under Rule 3
                # (kernel.py REJECTs a confidence "asserted without
                # derivation" outright, not REPAIR-downgrades it), and this
                # confidence value did come from somewhere - the agent's own
                # stated judgment of how much to trust each claim
                # (worker_agent/schema.py's `confidence` field). REASONING
                # is the honest fit among the three legal derivations
                # (EVIDENCE/REASONING/VERIFICATION): EVIDENCE and
                # VERIFICATION would overclaim a rigor the gate only
                # actually applied to *origin* above, not to this number.
                "derivation": "REASONING",
            }
        )
        edges.append([claim["id"], "conclusion", "SUPPORTS"])

    nodes.append(
        {
            "id": "conclusion",
            "type": "CONCLUSION",
            "label": proposal["rationale"][:200],
            # Set optimistically at 1.0; Rule 3's REPAIR mechanism
            # (kernel.py _apply_repair, rule==3) caps this down to the
            # graph's real support ceiling on commit() - that's Rule 3
            # working as designed, not something this translation needs
            # to compute itself. See this module's docstring / the Phase 3
            # report for a caveat about what that ceiling calc does and
            # does not account for.
            "confidence": 1.0,
            "derivation": "REASONING",
        }
    )

    return {"id": "quorum-proposal-claims", "nodes": nodes, "edges": edges}, agent_self_report


# ---------------------------------------------------------------------------
# Gate entry point
# ---------------------------------------------------------------------------


def _self_check_failed(proposal: dict[str, Any]) -> bool:
    """True only when proposal["self_check_result"]["passed"] is
    explicitly False - a proposal missing the field entirely (older
    fixtures, hand-built test proposals) is not treated as a failure,
    since self_check_result is a Worker Agent artifact, not something
    every caller of run_gate() is required to construct."""
    self_check = proposal.get("self_check_result")
    return isinstance(self_check, dict) and self_check.get("passed") is False


def run_gate(
    proposal: dict[str, Any],
    intent_graph: IntentGraph,
    audit: Optional[AuditLogger] = None,
    agent_id: str = "quorum-worker-agent",
    is_internal_redraft: bool = False,
) -> GateResult:
    """Runs one Proposal through Sentry, IntentGraph, and the Reasoning
    Kernel, logging every stage via Warden, and returns a verdict.

    `intent_graph` is passed in (not created here) so a caller running
    multiple proposals in one session - retry_gate() below, or a future
    multi-task session - can share re-entry history across them. There is
    no cross-process persistence for it yet; see this module's docstring
    in the Phase 3 report for why that's a Phase 4 (Firestore) concern,
    not something bolted on here.

    `is_internal_redraft`: True only for retry_gate()'s own automated
    redraft attempts (attempt > 1 within one retry_gate() call), never
    for a genuinely new, separate call. A redraft's task_description
    deliberately restates the prior REJECT's reasons ("A previous
    attempt was REJECTED for: ... Address this specifically in your
    redraft") - IntentGraph can legitimately score that as similar to
    the safety-boundary node retry_gate()'s own prior attempt just
    added, and flag HIGH re-entry risk against the system's own honest
    recovery attempt, not an external adversary reformulating a
    rejected idea. When True, the turn is still added to intent_graph
    (still visible to a FUTURE, separate call's re-entry check - Stage
    B's whole point) and Sentry/Kernel still fully gate the redraft's
    actual new content, but a HIGH IntentGraph score alone won't force
    ESCALATE on this specific redraft.

    `agent_id` identifies the real caller for the audit trail (see
    gate/agent_identity.py) - defaults to the pre-Agent-Identity value so
    every existing test and direct caller of run_gate() keeps working
    unchanged.
    """
    audit = audit or AuditLogger(root=str(DEFAULT_AUDIT_ROOT))
    now = lambda: int(datetime.now(timezone.utc).timestamp())  # noqa: E731
    reasons: list[str] = []
    # Cloud Trace is a real exported destination, outside this project's
    # own storage - PII in a span attribute would leave the system
    # entirely, not just sit in a database this code controls. Scrubbed
    # once, reused across all three stages' spans.
    traced_task = scrub_pii(proposal["task_description"][:200])

    # --- Stage A: Sentry, as a verifier of the agent's OWN output --------
    # Only the diff's added lines, plus the rationale in full - see
    # _added_lines()'s docstring for why scanning the whole diff false-
    # positives on exactly this project's own target repo.
    with stage_span("gate.sentry", **{"quorum.task": traced_task}) as sentry_span:
        scanned_text = _added_lines(proposal["diff"]) + "\n" + proposal["rationale"]
        sentry_result: PipelineResult = pipeline.run_input_stage(scanned_text)

        defragmented = _defragmented(scanned_text)
        if defragmented != scanned_text and sentry_result.action != PipelineAction.REJECT:
            defrag_result = pipeline.run_input_stage(defragmented)
            if defrag_result.action == PipelineAction.REJECT:
                sentry_result = defrag_result

        sentry_span.set_attribute("quorum.verdict", sentry_result.action.value)
        sentry_span.set_attribute("quorum.finding_count", len(sentry_result.findings))

        audit.log(
            agent_id=agent_id,
            objective=proposal["task_description"],
            status=f"sentry:{sentry_result.action.value}",
            tag=sentry_result.action.value,
            note=f"{len(sentry_result.findings)} finding(s) in the proposal's own diff/rationale",
            extra={"stage": "sentry", "findings": [f.rule for f in sentry_result.findings]},
        )

    # --- Stage B: IntentGraph - always record the turn, win or lose -------
    # so a later reformulated resubmission of a rejected objective is
    # visible to re-entry detection even on this exact attempt's failure.
    with stage_span("gate.intentgraph", **{"quorum.task": traced_task}) as intent_span:
        node = intent_graph.add_turn(proposal["task_description"], timestamp=now())
        intent_risk = "LOW"
        risk_explanation = ""
        if node is not None:
            risk_result = score_node(node, intent_graph)
            intent_risk = risk_result.risk
            risk_explanation = risk_result.explanation
            audit.log(
                agent_id=agent_id,
                objective=proposal["task_description"],
                status=f"intentgraph:{intent_risk}",
                tag=intent_risk,
                note=risk_explanation,
                extra={"stage": "intentgraph", "components": risk_result.components},
            )
        intent_span.set_attribute("quorum.risk", intent_risk)

    if sentry_result.action == PipelineAction.REJECT:
        reasons.append("Sentry found a HIGH-severity manipulation pattern in the proposal's own diff/rationale")
        intent_graph.add_turn("[SAFETY BOUNDARY TRIGGERED]", timestamp=now())
        return GateResult(
            verdict=GateVerdict.REJECT,
            reasons=reasons,
            sentry_action=sentry_result.action.value,
            intent_risk=intent_risk,
        )

    # --- Stage C: Reasoning Kernel - claim/provenance check ---------------
    with stage_span("gate.kernel", **{"quorum.task": traced_task}) as kernel_span:
        claim_graph, agent_self_report = build_claim_graph(proposal)
        kernel_result = pipeline.record_review_board_outcome(
            PipelineResult(action=PipelineAction.PROCEED_WITH_LOG), [claim_graph]
        )
        verdicts = kernel_result.claim_validation or []
        kernel_verdict: dict = verdicts[0] if verdicts else {"verdict": "ERROR", "detail": "no claim graph produced"}
        kernel_verdict_value = kernel_verdict.get("verdict")
        kernel_span.set_attribute("quorum.verdict", str(kernel_verdict_value))

        audit.log(
            agent_id=agent_id,
            objective=proposal["task_description"],
            status=f"kernel:{kernel_verdict_value}",
            tag=str(kernel_verdict_value),
            note=str(kernel_verdict.get("detail", "")),
            extra={"stage": "kernel", "agent_self_report": agent_self_report},
        )

    # Recorded either way (audit.log() above already ran), but a HIGH
    # score from retry_gate()'s own internal redraft attempt doesn't
    # force ESCALATE by itself - see is_internal_redraft's docstring.
    # Falls through to the kernel-verdict checks below either way, since
    # this isn't a mutually-exclusive "instead of," just "not blocking
    # for this specific reason."
    if intent_risk == "HIGH" and not is_internal_redraft:
        reasons.append(f"IntentGraph flagged HIGH re-entry risk: {risk_explanation}")
        gate_verdict = GateVerdict.ESCALATE
    elif kernel_verdict_value in _ESCALATE_KERNEL_VERDICTS:
        reasons.append(f"Reasoning Kernel verdict {kernel_verdict_value} - not clearly resolved, needs human deliberation")
        gate_verdict = GateVerdict.ESCALATE
    elif kernel_verdict_value in _REJECT_KERNEL_VERDICTS:
        reasons.append(f"Reasoning Kernel verdict {kernel_verdict_value}: {kernel_verdict.get('detail')}")
        gate_verdict = GateVerdict.REJECT
    elif _self_check_failed(proposal):
        # Found live: a proposal whose own tests never successfully ran
        # (e.g. self_check.py couldn't even execute pytest) previously
        # still reached PASS here, since Sentry/Kernel/IntentGraph only
        # evaluate the diff's *content*, never whether it actually works.
        # Missing/absent self_check_result is NOT treated as a failure
        # (existing callers construct minimal proposals without it) -
        # only an explicit passed=False is.
        self_check = proposal.get("self_check_result") or {}
        reasons.append(
            "Worker Agent's own self_check did not pass - "
            f"{self_check.get('pytest_summary', 'no summary available')}"
        )
        gate_verdict = GateVerdict.REJECT
    else:
        gate_verdict = GateVerdict.PASS

    if gate_verdict == GateVerdict.REJECT:
        intent_graph.add_turn("[SAFETY BOUNDARY TRIGGERED]", timestamp=now())
    elif gate_verdict == GateVerdict.ESCALATE:
        # Matches Trust Boundary's own architecture exactly: Review-Board
        # has zero programmatic API (docs/INTEGRATION_MAP.md Section 4) -
        # ESCALATE always means "pause for a human to run /review-board",
        # never an automated retry. Reused as-is, not reimplemented.
        pipeline.pause_for_review_board(
            PipelineResult(action=PipelineAction.PROCEED_WITH_LOG), reason="; ".join(reasons)
        )

    return GateResult(
        verdict=gate_verdict,
        reasons=reasons,
        sentry_action=sentry_result.action.value,
        kernel_verdict=kernel_verdict,
        intent_risk=intent_risk,
        agent_self_report=agent_self_report,
    )


def retry_gate(
    task_description: str,
    max_gate_attempts: int = 2,
    intent_graph: Optional[IntentGraph] = None,
    audit: Optional[AuditLogger] = None,
    agent_id: str = "quorum-worker-agent",
    persist_intent_graph: Optional[Callable[[], None]] = None,
) -> tuple[dict, GateResult, list[dict]]:
    """Runs the Worker Agent, then the gate; on REJECT, feeds the gate's
    reasons back to the Worker Agent for one redraft - a SEPARATE, outer
    loop from worker_agent's own internal self-check revision loop
    (worker_agent.orchestrator.MAX_ATTEMPTS). Stops immediately on PASS or
    ESCALATE - ESCALATE needs a human, not another automated attempt.

    `intent_graph`/`audit` are optional so a caller that's tracking a real
    session across multiple calls (e.g. a service layer holding one
    IntentGraph per user/session) can pass its own and get real re-entry
    detection across those calls. Omit either to get a fresh one per call
    (this function's original, self-contained behavior - what the test
    suite exercises).

    `agent_id` (see gate/agent_identity.py) identifies the real caller
    for the audit trail; defaults to the pre-Agent-Identity value.

    `persist_intent_graph`, if supplied, is called immediately after
    EVERY attempt - not just once after this whole function returns.
    This is deliberate, not an optimization: a REJECT's safety-boundary
    IntentGraph node must be durably saved BEFORE the next attempt's
    redraft begins, not bundled into one save that only happens once the
    entire retry sequence completes. Without this, a REJECT followed by
    the next attempt's own model call failing (WorkerAgentCallError)
    would lose the boundary marker entirely - it was added to the
    in-memory graph, but the caller's save (previously only reachable
    after retry_gate() returned normally) never got a chance to run.
    """
    from worker_agent.orchestrator import run_worker_agent  # deferred: keeps `import gate.quorum_gate` cheap

    intent_graph = intent_graph if intent_graph is not None else IntentGraph()
    audit = audit if audit is not None else AuditLogger(root=str(DEFAULT_AUDIT_ROOT))
    now = lambda: int(datetime.now(timezone.utc).timestamp())  # noqa: E731

    # --- Preflight: scan the RAW, caller-supplied task_description, ------
    # before it ever reaches Gemini. run_gate()'s own Sentry stage only
    # scans the agent's OWN diff/rationale, never the task that produced
    # them - a caller-supplied task containing PII or an injection
    # pattern would previously reach the Worker Agent, get echoed into
    # ADK's own session state, and only get caught (if at all) after a
    # real model call already happened. Reuses the identical Sentry
    # pipeline run_gate() uses (same ruleset - PII, injection patterns,
    # all of it), so nothing new to maintain here.
    preflight_result: PipelineResult = pipeline.run_input_stage(task_description)
    if preflight_result.action == PipelineAction.REJECT:
        audit.log(
            agent_id=agent_id,
            objective=task_description,
            status="preflight:REJECT",
            tag="REJECT",
            note=f"{len(preflight_result.findings)} finding(s) in the raw task description, before any model call",
            extra={"stage": "preflight", "findings": [f.rule for f in preflight_result.findings]},
        )
        intent_graph.add_turn("[SAFETY BOUNDARY TRIGGERED]", timestamp=now())
        return (
            {},
            GateResult(
                verdict=GateVerdict.REJECT,
                reasons=["Preflight: Sentry found a HIGH-severity pattern in the raw task description, before any Gemini call"],
                sentry_action=preflight_result.action.value,
            ),
            [],
        )

    description = task_description
    history: list[dict] = []
    proposal: dict = {}
    gate_result: Optional[GateResult] = None

    for attempt in range(1, max_gate_attempts + 1):
        proposal = run_worker_agent(description).model_dump()
        gate_result = run_gate(
            proposal,
            intent_graph=intent_graph,
            audit=audit,
            agent_id=agent_id,
            # See run_gate()'s is_internal_redraft docstring: only True
            # for attempt > 1, and only because THIS SAME retry_gate()
            # call's own prior REJECT put a boundary node in the graph -
            # never true for a caller's first attempt, which is exactly
            # as scrutinized by IntentGraph as it always was.
            is_internal_redraft=(attempt > 1),
        )
        history.append(
            {
                "attempt": attempt,
                "gate_verdict": gate_result.verdict.value,
                "reasons": gate_result.reasons,
            }
        )

        if persist_intent_graph is not None:
            persist_intent_graph()

        if gate_result.verdict != GateVerdict.REJECT:
            break

        if attempt < max_gate_attempts:
            description = (
                f"{task_description}\n\nA previous attempt was REJECTED by the gate for: "
                + "; ".join(gate_result.reasons)
                + ". Address this specifically in your redraft."
            )

    assert gate_result is not None
    return proposal, gate_result, history
