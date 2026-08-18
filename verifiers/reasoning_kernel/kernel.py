"""
Reasoning Kernel (RK) — deterministic integrity enforcement for reasoning graphs.

WHAT THIS IS
------------
An implementation of the eight core rules from TLOR Document 9. The kernel does
not reason. It decides whether a proposed change to a reasoning graph is legal.

Modules propose. The kernel disposes. Nothing enters the graph without passing
every rule, and nothing changes without an event in the log.

WHAT THIS IS NOT
----------------
Not a reasoning engine, not an RVM, not a semantic compiler. Those need an LLM
and therefore cannot be deterministic. This is the part that can be, and it is
the part with enforcement teeth.

THE ENFORCEMENT MODEL
---------------------
Three severities, because rejecting everything is useless:

  REJECT  the change is refused, transaction rolls back
  REPAIR  the change is admitted in a downgraded form, and the downgrade is
          logged as an event (a FACT with no provenance becomes an ASSUMPTION
          rather than being thrown away)
  WARN    admitted and flagged

REPAIR is the interesting one. TLOR Rule 1 says a fact without provenance
"becomes an assumption" — that is a repair, not an error, and it is what makes
the kernel usable by imperfect producers such as language models.
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ------------------------------------------------------------------ types

class NodeType(str, Enum):
    OBJECT = "OBJECT"
    ATTRIBUTE = "ATTRIBUTE"
    VALUE = "VALUE"
    GOAL = "GOAL"
    FACT = "FACT"
    ASSUMPTION = "ASSUMPTION"
    UNKNOWN = "UNKNOWN"
    CONSTRAINT = "CONSTRAINT"
    HYPOTHESIS = "HYPOTHESIS"
    CONCLUSION = "CONCLUSION"
    EVIDENCE = "EVIDENCE"


class EdgeType(str, Enum):
    HAS = "HAS"
    OWNS = "OWNS"
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    DEPENDS_ON = "DEPENDS_ON"
    CAUSES = "CAUSES"
    REQUIRES = "REQUIRES"
    EQUIVALENT = "EQUIVALENT"
    RELATES_TO = "RELATES_TO"
    INCONCLUSIVE = "INCONCLUSIVE"


class State(str, Enum):
    DRAFT = "DRAFT"
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    LOCKED = "LOCKED"
    ARCHIVED = "ARCHIVED"


class Perm(str, Enum):
    READ = "READ"
    WRITE = "WRITE"
    VERIFY = "VERIFY"
    ARCHIVE = "ARCHIVE"
    LOCK = "LOCK"
    DELETE = "DELETE"


class Origin(str, Enum):
    """How a fact came to be known. Maps to nlang 92xx evidential markers."""
    RETRIEVED = "RETRIEVED"        # 9206 verified against source
    USER_INPUT = "USER_INPUT"      # supplied by a human
    VERIFIED = "VERIFIED"          # checked by a verification module
    INFERRED = "INFERRED"          # 9201
    REPORTED = "REPORTED"          # 9202 secondhand
    GENERATED = "GENERATED"        # 9207 produced from model parameters


# Origins that can support a FACT. GENERATED and INFERRED cannot: a model
# producing a claim from its own weights is the definition of an unverified
# assertion, which is Rule 1.
FACT_ORIGINS = {Origin.RETRIEVED, Origin.USER_INPUT, Origin.VERIFIED}


class Derivation(str, Enum):
    """Where a confidence value came from. Rule 3 forbids ASSERTED."""
    EVIDENCE = "EVIDENCE"
    REASONING = "REASONING"
    VERIFICATION = "VERIFICATION"
    ASSERTED = "ASSERTED"          # illegal — confidence from nowhere


# ------------------------------------------------------------- structures

@dataclass
class Provenance:
    source: str
    origin: Origin
    timestamp: float = field(default_factory=time.time)


@dataclass
class Confidence:
    value: float
    derivation: Derivation
    refs: list = field(default_factory=list)


@dataclass
class Node:
    id: str
    type: NodeType
    label: str
    state: State = State.DRAFT
    provenance: Optional[Provenance] = None
    confidence: Optional[Confidence] = None
    contested: bool = False

    def __post_init__(self):
        # A FACT with no confidence is not "unknown confidence" — it is a
        # missing number that Rule 3's ceiling calculation (Kernel._cap)
        # will treat as zero support, silently capping every conclusion
        # resting on it at 0.0. Refuse to construct the node at all rather
        # than let that happen downstream.
        if self.type is not NodeType.FACT:
            return
        if self.confidence is None:
            raise FactConfidenceError(
                f"FACT node {self.id!r} ({self.label!r}) created without "
                f"confidence — pass an explicit value derived from its "
                f"source, e.g. Confidence(retrieval_score, Derivation.EVIDENCE) "
                f"for a retrieved document, or a user-declared certainty for "
                f"USER_INPUT. A FACT cannot carry confidence=None: Rule 3 uses "
                f"it as the support ceiling for every CONCLUSION/HYPOTHESIS "
                f"resting on this fact, and a missing value caps that ceiling "
                f"at 0.0 regardless of what the rest of the graph supports."
            )
        if not 0.0 <= self.confidence.value <= 1.0:
            raise FactConfidenceError(
                f"FACT node {self.id!r} ({self.label!r}) created with "
                f"confidence {self.confidence.value!r}, which is outside the "
                f"valid range 0.0-1.0 — source it from the same place the "
                f"rest of this fact's confidence came from (e.g. clamp a "
                f"retrieval_score, or fix the derivation upstream) rather "
                f"than passing an out-of-range number through."
            )


@dataclass
class Edge:
    src: str
    dst: str
    type: EdgeType


@dataclass
class Event:
    seq: int
    kind: str
    target: str
    detail: str
    module: str
    timestamp: float = field(default_factory=time.time)

    def __str__(self):
        return f"[{self.seq:03d}] {self.kind:<22} {self.target:<10} {self.detail}"


class Severity(str, Enum):
    REJECT = "REJECT"
    REPAIR = "REPAIR"
    WARN = "WARN"


@dataclass
class Violation:
    rule: int
    severity: Severity
    target: str
    message: str

    def __str__(self):
        return f"  R{self.rule} {self.severity.value:<6} {self.target:<10} {self.message}"


class KernelReject(Exception):
    def __init__(self, violations):
        self.violations = violations
        super().__init__(f"{len(violations)} rejection(s)")


class FactConfidenceError(ValueError):
    """A FACT node was constructed, or is being aggregated over, without a
    valid confidence value. Left unchecked, this silently caps every
    downstream CONCLUSION/HYPOTHESIS confidence at 0.0 in Rule 3's ceiling
    calculation (Kernel._cap) — a missing value is indistinguishable from
    "no support" once it drops out of that computation."""


# ---------------------------------------------------------------- kernel

class Kernel:
    """
    Governs a reasoning graph. Every mutation goes through a transaction;
    every transaction is validated against all eight rules before commit.
    """

    def __init__(self, propagation: str = "min"):
        self.nodes: dict = {}
        self.edges: list = []
        self.events: list = []
        self._seq = 0
        self._perms: dict = {}
        self._snapshot = None
        self._txn_module = None
        # 'min' assumes supporting claims are perfectly correlated;
        # 'product' assumes independence and decays much faster.
        # Neither is correct in general — see KERNEL.md.
        self.propagation = propagation

    # ------------------------------------------------------- bookkeeping

    def grant(self, module: str, perms: set):
        self._perms[module] = set(perms)

    def _check_perm(self, module: str, perm: Perm) -> bool:
        return perm in self._perms.get(module, set())

    def _log(self, kind: str, target: str, detail: str, module: str):
        self._seq += 1
        self.events.append(Event(self._seq, kind, target, detail, module))

    # ------------------------------------------------------ transactions

    def begin(self, module: str):
        if self._snapshot is not None:
            raise RuntimeError("transaction already open")
        self._snapshot = (copy.deepcopy(self.nodes), copy.deepcopy(self.edges))
        self._txn_module = module
        self._log("TRANSACTION_BEGIN", "-", f"module={module}", module)

    def rollback(self, reason: str = ""):
        if self._snapshot is None:
            return
        self.nodes, self.edges = self._snapshot
        self._log("TRANSACTION_ROLLED_BACK", "-", reason, self._txn_module or "-")
        self._snapshot = None
        self._txn_module = None

    def commit(self) -> list:
        """Validate, apply repairs, then commit — or roll back and raise."""
        violations = self.validate()
        rejects = [v for v in violations if v.severity is Severity.REJECT]

        if rejects:
            self.rollback(f"{len(rejects)} rule violation(s)")
            raise KernelReject(rejects)

        for v in violations:
            if v.severity is Severity.REPAIR:
                self._apply_repair(v)

        self._log("TRANSACTION_COMMITTED", "-",
                  f"{len(violations)} finding(s)", self._txn_module or "-")
        self._snapshot = None
        self._txn_module = None
        return violations

    def _apply_repair(self, v: Violation):
        n = self.nodes.get(v.target)
        if n is None:
            return
        if v.rule == 1 and n.type is NodeType.FACT:
            n.type = NodeType.ASSUMPTION
            self._log("NODE_DEMOTED", n.id, "FACT -> ASSUMPTION (no valid provenance)",
                      self._txn_module or "kernel")
        elif v.rule == 3 and n.confidence:
            old = n.confidence.value
            n.confidence.value = self._cap(n.id)
            self._log("CONFIDENCE_CAPPED", n.id,
                      f"{old:.2f} -> {n.confidence.value:.2f} (support ceiling)",
                      self._txn_module or "kernel")
        elif v.rule == 6:
            n.contested = True
            self._log("NODE_CONTESTED", n.id, "contradiction preserved",
                      self._txn_module or "kernel")

    # ----------------------------------------------------------- writing

    def add_node(self, module: str, node: Node):
        if not self._check_perm(module, Perm.WRITE):
            self._log("PERMISSION_DENIED", node.id, f"{module} lacks WRITE", module)
            raise PermissionError(f"{module} lacks WRITE")
        if node.id in self.nodes:
            raise ValueError(f"node {node.id} exists — Rule 7 forbids reuse of ids")
        self.nodes[node.id] = node
        self._log("NODE_CREATED", node.id, f"{node.type.value} {node.label!r}", module)

    def add_edge(self, module: str, edge: Edge):
        if not self._check_perm(module, Perm.WRITE):
            self._log("PERMISSION_DENIED", f"{edge.src}->{edge.dst}",
                      f"{module} lacks WRITE", module)
            raise PermissionError(f"{module} lacks WRITE")
        self.edges.append(edge)
        self._log("EDGE_CREATED", f"{edge.src}->{edge.dst}", edge.type.value, module)

    def update_node(self, module: str, node_id: str, **changes):
        n = self.nodes.get(node_id)
        if n is None:
            raise KeyError(node_id)
        if not self._check_perm(module, Perm.WRITE):
            raise PermissionError(f"{module} lacks WRITE")
        if n.state is State.LOCKED and not self._check_perm(module, Perm.LOCK):
            self._log("PERMISSION_DENIED", node_id, f"{module} cannot edit LOCKED", module)
            raise PermissionError(f"{node_id} is LOCKED")

        # Rule 4: provenance is immutable once attached.
        if "provenance" in changes and n.provenance is not None:
            if changes["provenance"] is None:
                self._log("PROVENANCE_STRIP_REFUSED", node_id,
                          "Rule 4 — provenance is immutable", module)
                raise KernelReject([Violation(4, Severity.REJECT, node_id,
                                              "provenance cannot be removed")])
            self._log("PROVENANCE_CHANGED", node_id,
                      f"{n.provenance.source} -> {changes['provenance'].source}", module)

        # Rule 7: identity is stable. Labels may change, ids may not.
        if "id" in changes:
            raise KernelReject([Violation(7, Severity.REJECT, node_id,
                                          "node id is immutable")])

        for k, val in changes.items():
            old = getattr(n, k, None)
            setattr(n, k, val)
            self._log("NODE_UPDATED", node_id, f"{k}: {old!r} -> {val!r}", module)

    def delete_node(self, module: str, node_id: str):
        n = self.nodes.get(node_id)
        if n is None:
            raise KeyError(node_id)
        if not self._check_perm(module, Perm.DELETE):
            self._log("PERMISSION_DENIED", node_id, f"{module} lacks DELETE", module)
            raise PermissionError(f"{module} lacks DELETE")
        # Rule 6: a node in a contradiction may never be deleted.
        if any(e.type is EdgeType.CONTRADICTS and node_id in (e.src, e.dst)
               for e in self.edges):
            self._log("DELETE_REFUSED", node_id,
                      "Rule 6 — node participates in a contradiction", module)
            raise KernelReject([Violation(6, Severity.REJECT, node_id,
                                          "contradicted nodes are preserved, not deleted")])
        n.state = State.ARCHIVED
        self._log("NODE_ARCHIVED", node_id, "delete became archive — Rule 8", module)

    # ------------------------------------------------------------- rules

    def _support_nodes(self, node_id: str) -> list:
        """Nodes this one rests on: incoming SUPPORTS, outgoing DEPENDS_ON.
        Used specifically for Rule 3's confidence ceiling — a CONTRADICTS
        edge doesn't cap confidence the same way a SUPPORTS edge does, so
        it deliberately stays out of this one."""
        out = []
        for e in self.edges:
            if e.type is EdgeType.SUPPORTS and e.dst == node_id:
                out.append(e.src)
            elif e.type is EdgeType.DEPENDS_ON and e.src == node_id:
                out.append(e.dst)
        return out

    def _grounding_nodes(self, node_id: str) -> list:
        """Broader than _support_nodes — anything that explains why this
        node has the value it has, for Rule 5's traceability check.

        A CONCLUSION reached via 'this contradicts the evidence' is just as
        traceable as one reached via 'this is supported by the evidence' —
        refutation is a form of grounding, not an absence of it. INCONCLUSIVE
        is grounding too: 'this evidence exists but doesn't resolve the
        claim' is itself a reasoning path, and it's the one an honest NEI
        abstention should take. Found by running real model output: two
        different models (Claude, Gemini), independently and without seeing
        each other's output, both reached for a third relation beyond
        SUPPORTS/CONTRADICTS to express exactly this — they just named it
        differently (NEUTRAL, REFUTES, INSUFFICIENT_EVIDENCE) until the
        schema gave INCONCLUSIVE a fixed name."""
        out = []
        for e in self.edges:
            if e.dst == node_id and e.type in (EdgeType.SUPPORTS, EdgeType.CONTRADICTS,
                                               EdgeType.INCONCLUSIVE):
                out.append(e.src)
            elif e.type is EdgeType.DEPENDS_ON and e.src == node_id:
                out.append(e.dst)
        return out

    def _cap(self, node_id: str) -> float:
        """Ceiling on confidence imposed by whatever this node rests on."""
        vals = []
        for s in self._support_nodes(node_id):
            supporter = self.nodes.get(s)
            if supporter is None:
                continue
            if supporter.type is NodeType.FACT and supporter.confidence is None:
                # Node.__post_init__ should make this unreachable — a FACT
                # cannot be constructed without confidence. Getting here
                # means something bypassed that check (a mutated attribute,
                # or a graph loaded/deserialized without going through
                # Node()). Fail loudly instead of letting this silently
                # drop out of `vals` and cap node_id's confidence at 0.0.
                raise FactConfidenceError(
                    f"Rule 3 aggregation for {node_id!r} rests on FACT node "
                    f"{s!r} which has confidence=None. This should be "
                    f"impossible for a FACT — re-derive {s!r}'s confidence "
                    f"from its source (retrieval_score, user-declared "
                    f"certainty, etc.) and reconstruct it through Node(), "
                    f"or check whatever loaded this graph for a bypass of "
                    f"FACT confidence validation."
                )
            if supporter.confidence is not None:
                vals.append(supporter.confidence.value)
        if not vals:
            return 0.0
        if self.propagation == "product":
            out = 1.0
            for v in vals:
                out *= v
            return out
        return min(vals)

    def _has_cycle(self) -> list:
        """Cycles in the grounding relation. Same 'what does X rest on'
        relation _support_nodes computes for Rule 3's ceiling (DEPENDS_ON:
        src rests on dst; SUPPORTS: dst rests on src) — a cycle here means
        circular justification, e.g. A supports B and B supports A, each
        citing the other as its reason to exist. Iterative DFS — recursion
        is a liability here."""
        adj = {}
        for e in self.edges:
            if e.type is EdgeType.DEPENDS_ON:
                adj.setdefault(e.src, []).append(e.dst)
            elif e.type is EdgeType.SUPPORTS:
                adj.setdefault(e.dst, []).append(e.src)
        WHITE, GREY, BLACK = 0, 1, 2
        colour = {n: WHITE for n in self.nodes}
        found = []
        for start in list(colour):
            if colour[start] != WHITE:
                continue
            stack = [(start, iter(adj.get(start, [])))]
            colour[start] = GREY
            path = [start]
            while stack:
                node, it = stack[-1]
                nxt = next(it, None)
                if nxt is None:
                    colour[node] = BLACK
                    stack.pop()
                    path.pop()
                    continue
                if colour.get(nxt) == GREY:
                    found.append(" -> ".join(path[path.index(nxt):] + [nxt]))
                elif colour.get(nxt) == WHITE:
                    colour[nxt] = GREY
                    path.append(nxt)
                    stack.append((nxt, iter(adj.get(nxt, []))))
        return found

    def validate(self) -> list:
        v = []
        contradicted = {
            nid for e in self.edges if e.type is EdgeType.CONTRADICTS
            for nid in (e.src, e.dst)
        }

        for n in self.nodes.values():
            if n.state is State.ARCHIVED:
                continue

            # Rule 1 — facts cannot be invented.
            if n.type is NodeType.FACT:
                if n.provenance is None:
                    v.append(Violation(1, Severity.REPAIR, n.id,
                                       "FACT has no provenance"))
                elif n.provenance.origin not in FACT_ORIGINS:
                    v.append(Violation(1, Severity.REPAIR, n.id,
                                       f"FACT origin {n.provenance.origin.value} "
                                       f"is not verifiable"))

            # Rule 2 — unknown remains unknown.
            if n.type is NodeType.UNKNOWN and n.confidence is not None:
                v.append(Violation(2, Severity.REJECT, n.id,
                                   "UNKNOWN carries a confidence value"))

            # Rule 3 — confidence never appears from nowhere.
            if n.confidence is not None:
                c = n.confidence
                if c.derivation is Derivation.ASSERTED:
                    v.append(Violation(3, Severity.REJECT, n.id,
                                       "confidence asserted without derivation"))
                elif not 0.0 <= c.value <= 1.0:
                    v.append(Violation(3, Severity.REJECT, n.id,
                                       f"confidence {c.value} out of range"))
                elif n.type in (NodeType.CONCLUSION, NodeType.HYPOTHESIS):
                    ceiling = self._cap(n.id)
                    if c.value > ceiling + 1e-9:
                        v.append(Violation(3, Severity.REPAIR, n.id,
                                           f"confidence {c.value:.2f} exceeds support "
                                           f"ceiling {ceiling:.2f}"))
            elif n.type is NodeType.ASSUMPTION:
                v.append(Violation(3, Severity.WARN, n.id,
                                   "ASSUMPTION has no confidence"))

            # Rule 5 — every conclusion is traceable.
            if n.type is NodeType.CONCLUSION and not self._grounding_nodes(n.id):
                v.append(Violation(5, Severity.REJECT, n.id,
                                   "CONCLUSION has no supporting path"))

            # Rule 6 — contradictions are preserved and marked.
            if n.id in contradicted and not n.contested:
                v.append(Violation(6, Severity.REPAIR, n.id,
                                   "participates in a contradiction, not marked"))

        # dangling edges
        for e in self.edges:
            for end in (e.src, e.dst):
                if end not in self.nodes:
                    v.append(Violation(8, Severity.REJECT, end,
                                       f"edge {e.src}->{e.dst} references missing node"))

        for c in self._has_cycle():
            v.append(Violation(8, Severity.REJECT, c.split(" -> ")[0],
                               f"circular grounding (SUPPORTS/DEPENDS_ON): {c}"))

        return v

    # ------------------------------------------------------------ report

    def trace(self, node_id: str, depth: int = 0) -> str:
        """Rule 5 in readable form — what does this conclusion rest on?"""
        n = self.nodes.get(node_id)
        if n is None:
            return f"{'  ' * depth}? {node_id}"
        conf = f" [{n.confidence.value:.2f} via {n.confidence.derivation.value}]" \
               if n.confidence else ""
        prov = f" <- {n.provenance.source} ({n.provenance.origin.value})" \
               if n.provenance else ""
        mark = " *CONTESTED*" if n.contested else ""
        line = f"{'  ' * depth}{n.type.value:<11} {n.label}{conf}{prov}{mark}"
        for s in self._support_nodes(node_id):
            line += "\n" + self.trace(s, depth + 1)
        return line
