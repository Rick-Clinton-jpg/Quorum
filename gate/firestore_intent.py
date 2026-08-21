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

import logging
import os
from typing import Any, Optional

import numpy as np

from intent_layer import Edge, IntentGraph, IntentNode

logger = logging.getLogger(__name__)

try:
    from google.cloud import firestore

    FIRESTORE_AVAILABLE = True
except ImportError:
    FIRESTORE_AVAILABLE = False


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

        if FIRESTORE_AVAILABLE:
            try:
                self.db = firestore.Client(project=self.project)
            except Exception as exc:  # noqa: BLE001 - any init failure means "use local fallback"
                logger.warning("Firestore client init failed for IntentStore (%s) - using local memory.", exc)

    def load_session(self, session_id: str) -> IntentGraph:
        """A fresh IntentGraph() for a session never seen before - matches
        IntentGraph's own no-argument constructor, not a special case."""
        if self.db is not None:
            try:
                doc = self.db.collection(self.collection_name).document(session_id).get()
                if doc.exists:
                    return deserialize_intent_graph(doc.to_dict())
                return IntentGraph()
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to load session %r from Firestore (%s) - trying local fallback.", session_id, exc)

        if session_id in self._local_sessions:
            return deserialize_intent_graph(self._local_sessions[session_id])
        return IntentGraph()

    def save_session(self, session_id: str, graph: IntentGraph) -> None:
        data = serialize_intent_graph(graph)
        if self.db is not None:
            try:
                self.db.collection(self.collection_name).document(session_id).set(data)
                return
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to save session %r to Firestore (%s) - using local fallback.", session_id, exc)

        self._local_sessions[session_id] = data
