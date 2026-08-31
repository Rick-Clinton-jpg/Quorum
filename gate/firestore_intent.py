"""Firestore-backed IntentGraph session persistence. New, Quorum-owned
code; does not modify the vendored intent_graph package
(verifiers/intent_graph/).

Serializes/deserializes against IntentGraph's REAL shape
(verifiers/intent_graph/intent_layer/graph.py), confirmed by reading that
file directly, not assumed:

  IntentGraph.nodes: List[IntentNode]   (a list, not a dict)
  IntentGraph.edges: List[Edge]         (a list, not a dict)
  IntentNode: intent_id, description, embedding (np.ndarray), timestamp,
              confidence, direction, safety_boundary, parent_intent,
              domain, lineage_root, is_reformulation_cue,
              is_backreference_cue
  Edge: source, target, edge_type (a string, e.g. "boundary_encounter")
        - Edge has NO similarity field.

`safety_boundary` and `lineage_root` are not incidental fields - they are
the entire mechanism re-entry detection runs on. scorer.py's hard gate
refuses to elevate risk above LOW without a real safety_boundary=True
node sharing the querying node's lineage_root
(IntentGraph.lineage_boundary_nodes()). Drop either field on a
serialize/deserialize round trip and every node comes back with
safety_boundary reset to its dataclass-implied default - re-entry
detection then silently stops working for every session, forever, with
no exception raised anywhere. This is the one thing to check first if
IntentGraph ever "works" after a first save but never flags anything
after a reload.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional

import numpy as np

from intent_layer import Edge, IntentGraph, IntentNode

logger = logging.getLogger(__name__)

try:
    from google.cloud import firestore

    FIRESTORE_AVAILABLE = True
except ImportError:
    FIRESTORE_AVAILABLE = False

# Firestore's real hard cap is 1 MiB (1,048,576 bytes) per document,
# server-enforced, every field included. Confirmed live to matter, not
# hypothetical: production (service/requirements.txt deliberately
# excludes sentence-transformers - see that file's own comment) always
# runs IntentExtractor's HashingVectorizer fallback, n_features=2048 -
# one node's embedding array alone serializes to roughly 40KB of JSON,
# so a single session's graph can approach Firestore's limit in well
# under a hundred turns, not thousands. _estimate_bytes()'s json.dumps
# length is a conservative proxy for Firestore's own wire encoding
# (mostly float arrays) - this budget leaves real headroom below the
# hard cap rather than targeting it exactly.
_FIRESTORE_SAFE_BYTES = 700_000


def _estimate_bytes(data: dict[str, Any]) -> int:
    return len(json.dumps(data).encode("utf-8"))


def _prune_for_firestore(data: dict[str, Any], max_bytes: int) -> tuple[dict[str, Any], int]:
    """Drops the OLDEST non-safety-boundary nodes (oldest first, by
    append order) until the serialized result fits under max_bytes.
    NEVER drops a safety_boundary node - re-entry detection's actual
    security guarantee (scorer.py's lineage_boundary_nodes) depends on
    every one of those existing forever; see this module's own
    docstring. Ordinary embedding-similarity matching (_best_match)
    over older, non-boundary turns is best-effort and degrades
    gracefully as they age out - that's the trade this function makes.

    Returns (data, nodes_dropped) so save_session can log honestly
    when a session's history was actually cut down for Firestore,
    rather than silently truncating it. Never touches `data` itself -
    the caller's own in-memory graph and local fallback copy keep the
    full, unpruned history regardless."""
    if _estimate_bytes(data) <= max_bytes:
        return data, 0

    nodes = data["nodes"]  # append order == oldest first
    overage = _estimate_bytes(data) - max_bytes
    drop_ids: set[str] = set()
    reclaimed = 0
    for n in nodes:
        if reclaimed >= overage:
            break
        if n["safety_boundary"]:
            continue
        drop_ids.add(n["intent_id"])
        reclaimed += len(json.dumps(n).encode("utf-8"))

    if not drop_ids:
        # Nothing safe left to prune (e.g. every node is a safety
        # boundary) - return unchanged and let the caller's own
        # Firestore write fail loudly rather than silently dropping one.
        return data, 0

    pruned = {
        **data,
        "nodes": [n for n in nodes if n["intent_id"] not in drop_ids],
        "edges": [e for e in data["edges"] if e["source"] not in drop_ids and e["target"] not in drop_ids],
    }
    return pruned, len(drop_ids)


def merge_intent_graphs(primary: IntentGraph, fallback: IntentGraph) -> IntentGraph:
    """Unions two IntentGraphs for the SAME session into one - used when
    a single process finds both a Firestore record and a not-yet-
    reconciled local fallback copy for the same session_id (an earlier
    save_session() call this process made hit a live Firestore failure
    and fell back to memory; a later load finds Firestore reachable
    again). Never silently prefers one source over the other: trusting
    Firestore blindly the moment it recovers would quietly cost every
    turn this process recorded locally during the outage.

    Nodes are append-only and never mutated after creation (see
    graph.py's add_turn()), so a shared intent_id USUALLY is the same
    node, and the common case just dedupes it. Confirmed as a real gap
    by independent re-audit: the previous version assumed that was
    ALWAYS true and let `primary` silently win on any id collision - but
    session_lock() only protects one process's own load-mutate-save
    sequence (see its docstring and firestore_intent.py's IntentStore
    docstring), not two DIFFERENT processes/Cloud Run instances racing
    on the same session_id with a plain Firestore .set() in between (a
    blind overwrite, not a compare-and-swap - see save_session()). Under
    that race, `primary` and `fallback` can mint the SAME id for two
    ACTUALLY DIFFERENT turns, and silently keeping only `primary` would
    quietly discard a real, distinct turn - including, worst case, a
    safety-boundary one. When a collision's content genuinely differs
    (not just an identical duplicate), the fallback node is kept too,
    under a freshly minted id, rather than dropped - and every OTHER
    fallback node's own parent_intent/lineage_root/edges are remapped
    through the same rename so the fallback's internal lineage chain
    stays consistent after the rename. (This doesn't make the
    underlying race impossible - the real fix is process-external
    coordination, e.g. a Firestore transaction around the whole
    load-mutate-save cycle, still open work - it only makes this merge
    step stop being the place a real turn quietly vanishes.)"""
    from dataclasses import replace

    by_id: dict[str, IntentNode] = {n.intent_id: n for n in primary.nodes}
    next_id = max(primary._next_id, fallback._next_id)
    id_remap: dict[str, str] = {}

    for n in fallback.nodes:
        existing = by_id.get(n.intent_id)
        if existing is None:
            by_id[n.intent_id] = n
            continue
        if existing.description == n.description and existing.timestamp == n.timestamp:
            continue  # same id, same content - a genuine duplicate, safe to dedupe
        new_id = f"n{next_id}"
        next_id += 1
        id_remap[n.intent_id] = new_id
        by_id[new_id] = replace(n, intent_id=new_id)

    def _remapped(node_id: Optional[str]) -> Optional[str]:
        return id_remap.get(node_id, node_id) if node_id is not None else None

    # Re-thread every fallback-only node's own internal references (not
    # just the renamed one's) through the rename, so a fallback node
    # created AFTER a colliding one - pointing at it as its parent or
    # lineage root - doesn't end up referencing an id that now belongs
    # to `primary`'s unrelated node instead.
    if id_remap:
        for node_id in list(by_id):
            if node_id in id_remap.values() or node_id in [n.intent_id for n in fallback.nodes]:
                n = by_id[node_id]
                by_id[node_id] = replace(
                    n,
                    parent_intent=_remapped(n.parent_intent),
                    lineage_root=_remapped(n.lineage_root) or n.lineage_root,
                )

    merged = IntentGraph()
    merged.nodes = sorted(by_id.values(), key=lambda n: n.timestamp)

    seen_edges: set[tuple[str, str, str]] = set()
    merged.edges = []
    for e in primary.edges:
        key = (e.source, e.target, e.edge_type)
        if key not in seen_edges:
            seen_edges.add(key)
            merged.edges.append(e)
    for e in fallback.edges:
        remapped = Edge(_remapped(e.source), _remapped(e.target), e.edge_type)
        key = (remapped.source, remapped.target, remapped.edge_type)
        if key not in seen_edges:
            seen_edges.add(key)
            merged.edges.append(remapped)

    merged._next_id = next_id
    return merged


def serialize_intent_graph(graph: IntentGraph) -> dict[str, Any]:
    """IntentGraph -> a Firestore-safe dict. Every IntentNode/Edge field
    is carried through explicitly - nothing is dropped or guessed."""
    return {
        "nodes": [
            {
                "intent_id": n.intent_id,
                "description": n.description,
                "embedding": n.embedding.astype(float).tolist(),
                "timestamp": n.timestamp,
                "confidence": n.confidence,
                "direction": n.direction,
                "safety_boundary": n.safety_boundary,
                "parent_intent": n.parent_intent,
                "domain": n.domain,
                "lineage_root": n.lineage_root,
                "is_reformulation_cue": n.is_reformulation_cue,
                "is_backreference_cue": n.is_backreference_cue,
            }
            for n in graph.nodes
        ],
        "edges": [
            {"source": e.source, "target": e.target, "edge_type": e.edge_type} for e in graph.edges
        ],
        # IntentGraph._new_id() mints "n{next_id}" for every new node created
        # via add_turn(). Without restoring this, a reloaded graph starts
        # counting from 0 again and can mint an id that collides with an
        # existing node's intent_id.
        "next_id": graph._next_id,
    }


def deserialize_intent_graph(data: dict[str, Any]) -> IntentGraph:
    """The inverse of serialize_intent_graph(). Rebuilds real IntentNode/
    Edge instances (not dicts) so every IntentGraph method
    (lineage_boundary_nodes, most_recent_boundary_node, _best_match,
    add_turn's own parent-resolution logic) keeps working exactly as it
    does on a graph that was never persisted."""
    graph = IntentGraph()
    graph.nodes = [
        IntentNode(
            intent_id=n["intent_id"],
            description=n["description"],
            embedding=np.asarray(n["embedding"], dtype=np.float32),
            timestamp=n["timestamp"],
            confidence=n["confidence"],
            direction=n["direction"],
            safety_boundary=n["safety_boundary"],
            parent_intent=n.get("parent_intent"),
            domain=n["domain"],
            lineage_root=n["lineage_root"],
            is_reformulation_cue=n.get("is_reformulation_cue", False),
            is_backreference_cue=n.get("is_backreference_cue", False),
        )
        for n in data.get("nodes", [])
    ]
    graph.edges = [
        Edge(source=e["source"], target=e["target"], edge_type=e["edge_type"])
        for e in data.get("edges", [])
    ]
    graph._next_id = data.get("next_id", len(graph.nodes))
    return graph


class FirestoreIntentStore:
    """Persists/retrieves one IntentGraph per session_id. Falls back to an
    in-memory dict (per-process only - fine for local dev/tests, not a
    substitute for real persistence across Cloud Run instances) when
    Firestore is unavailable or a live call fails."""

    def __init__(self, collection_name: str = "quorum_intent_sessions", project: Optional[str] = None):
        self.collection_name = collection_name
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.db = None
        self._local_sessions: dict[str, dict[str, Any]] = {}

        # The real race isn't inside this class - it's the load -> mutate
        # -> save sequence that spans a whole request in service/main.py:
        # load_session() returns a graph, gate/quorum_gate.py mutates it
        # in place (add_turn()), then save_session() overwrites whatever
        # was there. Two requests racing on the SAME session_id can lose
        # one's update - and, confirmed by independent re-audit, this is
        # NOT limited to the local-fallback path: save_session()'s real
        # Firestore write is a plain `.set()`, a blind overwrite, not a
        # compare-and-swap, so the same lost-update race exists against
        # live Firestore too, whenever two DIFFERENT Cloud Run instances
        # (not just two threads in this one process) race on the same
        # session_id. A lock on save_session() alone can't fix that - the
        # stale read already happened by then. session_lock() below is
        # what a caller holds across the whole load-to-save sequence, but
        # (see its own docstring) only closes this race within one
        # process/instance - the cross-instance case is still open;
        # merge_intent_graphs() at least keeps that case from silently
        # discarding a real, divergent turn on its next reconciling load.
        # _locks_guard only protects creating a new per-session lock,
        # not the sessions themselves.
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

        if FIRESTORE_AVAILABLE:
            try:
                self.db = firestore.Client(project=self.project)
            except Exception as exc:  # noqa: BLE001 - any init failure means "use local fallback"
                logger.warning("Firestore client init failed for IntentStore (%s) - using local memory.", exc)

    def load_session(self, session_id: str) -> IntentGraph:
        """A fresh IntentGraph() for a session never seen before - matches
        IntentGraph's own no-argument constructor, not a special case.

        If a real Firestore record AND a local fallback copy both exist
        for this session_id (an earlier save_session() call on this
        process hit a live Firestore failure and fell back to memory,
        and Firestore is reachable again now), merges them honestly via
        merge_intent_graphs() instead of silently preferring whichever
        source happened to be checked first - see that function's
        docstring for why a blind preference would cost real turns."""
        firestore_graph: Optional[IntentGraph] = None
        if self.db is not None:
            try:
                doc = self.db.collection(self.collection_name).document(session_id).get()
                if doc.exists:
                    firestore_graph = deserialize_intent_graph(doc.to_dict())
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to load session %r from Firestore (%s) - trying local fallback.", session_id, exc)

        local_data = self._local_sessions.get(session_id)
        local_graph = deserialize_intent_graph(local_data) if local_data is not None else None

        if firestore_graph is not None and local_graph is not None:
            merged = merge_intent_graphs(firestore_graph, local_graph)
            logger.warning(
                "Session %r had both a Firestore record (%d node(s)) and an unreconciled local "
                "fallback copy (%d node(s)) - merged honestly into %d node(s) rather than "
                "silently preferring one.",
                session_id, len(firestore_graph.nodes), len(local_graph.nodes), len(merged.nodes),
            )
            self._local_sessions.pop(session_id, None)  # reconciled - stop treating it as still-pending
            return merged
        if firestore_graph is not None:
            return firestore_graph
        if local_graph is not None:
            return local_graph
        return IntentGraph()

    def save_session(self, session_id: str, graph: IntentGraph) -> None:
        data = serialize_intent_graph(graph)
        if self.db is not None:
            write_data, dropped = _prune_for_firestore(data, max_bytes=_FIRESTORE_SAFE_BYTES)
            if dropped:
                logger.warning(
                    "Session %r's graph (%d node(s)) exceeded Firestore's safe size budget "
                    "(%d bytes) - dropped %d oldest non-safety-boundary node(s) before writing "
                    "(every safety_boundary node was kept). This process's own in-memory "
                    "IntentGraph, and the local fallback copy if one is written below, both "
                    "keep the full, unpruned history.",
                    session_id, len(graph.nodes), _FIRESTORE_SAFE_BYTES, dropped,
                )
            try:
                self.db.collection(self.collection_name).document(session_id).set(write_data)
                return
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to save session %r to Firestore (%s) - using local fallback.", session_id, exc)

        # Local fallback always keeps the FULL, unpruned graph - pruning
        # is purely a Firestore document-size accommodation, not
        # something the in-memory fallback needs.
        self._local_sessions[session_id] = data

    @contextmanager
    def session_lock(self, session_id: str) -> Iterator[None]:
        """Held by the caller across a whole load_session() ->
        (mutate the graph) -> save_session() sequence, not just around
        save_session() itself - that's the only way to actually close
        the lost-update race described above, not just narrow it.

        This is a `threading.Lock()` - process-local by construction.
        It fully closes the race between two REQUESTS handled by
        different THREADS in the same process (Cloud Run's default
        concurrency serves multiple requests per instance this way),
        which covers the common case and is why holding it costs
        nothing either way. It does NOT, and cannot, coordinate across
        two different Cloud Run INSTANCES (separate processes, no
        shared memory) - confirmed as a real gap by independent
        re-audit, not a hypothetical: under sustained load Cloud Run
        autoscaling can and does route two requests for the same
        session_id to two different instances, each with its own,
        mutually invisible lock. A plain Firestore `.set()` in
        save_session() below is a blind overwrite, not a compare-and-
        swap, so that cross-instance race is still open - the real fix
        is a Firestore transaction wrapping the whole load-mutate-save
        cycle (not yet implemented), not this lock. merge_intent_graphs()
        above at least stops that race's worst silent-data-loss case
        (two colliding-id nodes merging into one) from being silent."""
        with self._locks_guard:
            lock = self._locks.setdefault(session_id, threading.Lock())
        with lock:
            yield
