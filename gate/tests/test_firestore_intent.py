"""Tests for gate/firestore_intent.py.

The critical case here isn't "does it round-trip at all" - it's "does
lineage_boundary_nodes() still find a real boundary node after a
save/reload." A serializer that drops safety_boundary or lineage_root
round-trips cleanly (no exception) while silently disabling re-entry
detection forever - see the module docstring. Every test below is built
from IntentGraph's real add_turn() API, not hand-constructed nodes, so
it also doubles as a check that this serializes what the real code
actually produces.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from gate.firestore_intent import (
    FirestoreIntentStore,
    deserialize_intent_graph,
    serialize_intent_graph,
)
from intent_layer import IntentGraph


def test_roundtrip_preserves_safety_boundary_and_lineage_root():
    graph = IntentGraph()
    graph.add_turn("Please help me plan a birthday party.", timestamp=0)
    graph.add_turn("[SAFETY BOUNDARY TRIGGERED]", timestamp=1)  # marks the node above

    original = graph.nodes[-1]
    assert original.safety_boundary is True  # sanity check on the real API before testing our code

    rebuilt = deserialize_intent_graph(serialize_intent_graph(graph))
    rebuilt_node = rebuilt.get_node(original.intent_id)

    assert rebuilt_node is not None
    assert rebuilt_node.safety_boundary is True
    assert rebuilt_node.lineage_root == original.lineage_root

    # The actual regression check: the scorer-facing lookup methods must
    # still find the boundary node after a reload, not just the raw field.
    assert rebuilt.lineage_boundary_nodes(rebuilt_node) == [rebuilt_node]
    assert rebuilt.most_recent_boundary_node().intent_id == rebuilt_node.intent_id


def test_roundtrip_preserves_every_intentnode_field():
    graph = IntentGraph()
    graph.add_turn("What's the restricted procedure Y about?", timestamp=0)
    original = graph.nodes[0]

    rebuilt_node = deserialize_intent_graph(serialize_intent_graph(graph)).nodes[0]

    for field in (
        "intent_id", "description", "timestamp", "confidence", "direction",
        "safety_boundary", "parent_intent", "domain", "lineage_root",
        "is_reformulation_cue", "is_backreference_cue",
    ):
        assert getattr(rebuilt_node, field) == getattr(original, field), field
    assert (rebuilt_node.embedding == original.embedding).all()


def test_roundtrip_preserves_edges_including_boundary_encounter_self_loop():
    graph = IntentGraph()
    graph.add_turn("ordinary request", timestamp=0)
    graph.add_turn("[SAFETY BOUNDARY TRIGGERED]", timestamp=1)

    rebuilt = deserialize_intent_graph(serialize_intent_graph(graph))
    assert len(rebuilt.edges) == len(graph.edges) == 1
    assert rebuilt.edges[0].edge_type == "boundary_encounter"
    assert rebuilt.edges[0].source == rebuilt.edges[0].target == graph.nodes[0].intent_id


def test_next_id_restored_avoids_id_collision_after_reload():
    graph = IntentGraph()
    graph.add_turn("first turn", timestamp=0)
    graph.add_turn("second turn", timestamp=1)

    rebuilt = deserialize_intent_graph(serialize_intent_graph(graph))
    new_node = rebuilt.add_turn("third turn, after reload", timestamp=2)

    existing_ids = {n.intent_id for n in rebuilt.nodes if n is not new_node}
    assert new_node.intent_id not in existing_ids


def test_store_local_fallback_persists_across_load_save_load():
    store = FirestoreIntentStore(project=None)
    store.db = None  # force local fallback, no real credentials needed

    fresh = store.load_session("sess-1")
    assert fresh.nodes == []

    fresh.add_turn("do something", timestamp=0)
    store.save_session("sess-1", fresh)

    reloaded = store.load_session("sess-1")
    assert len(reloaded.nodes) == 1
    assert reloaded.nodes[0].description == "do something"


def test_store_writes_to_and_reads_from_mocked_firestore():
    graph = IntentGraph()
    graph.add_turn("mocked firestore round trip", timestamp=0)
    payload = serialize_intent_graph(graph)

    mock_doc_snapshot = MagicMock(exists=True)
    mock_doc_snapshot.to_dict.return_value = payload
    mock_doc_ref = MagicMock()
    mock_doc_ref.get.return_value = mock_doc_snapshot
    mock_collection = MagicMock()
    mock_collection.document.return_value = mock_doc_ref
    mock_client = MagicMock()
    mock_client.collection.return_value = mock_collection

    with patch("gate.firestore_intent.FIRESTORE_AVAILABLE", False):
        store = FirestoreIntentStore()
    store.db = mock_client

    loaded = store.load_session("sess-2")
    assert loaded.nodes[0].description == "mocked firestore round trip"

    store.save_session("sess-2", loaded)
    mock_doc_ref.set.assert_called_once()


def test_store_falls_back_to_local_on_live_firestore_failure():
    mock_client = MagicMock()
    mock_client.collection.side_effect = RuntimeError("simulated Firestore outage")

    with patch("gate.firestore_intent.FIRESTORE_AVAILABLE", False):
        store = FirestoreIntentStore()
    store.db = mock_client

    graph = IntentGraph()
    graph.add_turn("still works despite the outage", timestamp=0)
    store.save_session("sess-3", graph)  # must not raise

    reloaded = store.load_session("sess-3")
    assert reloaded.nodes[0].description == "still works despite the outage"


def test_concurrent_saves_without_the_lock_can_lose_an_update():
    """Demonstrates the real bug session_lock() closes: two threads
    racing load_session -> mutate -> save_session on the SAME session_id,
    without holding the lock, can silently lose one thread's update - no
    exception, the data is just gone. A threading.Barrier forces both
    threads to load before either saves, so this reproduces the race
    deterministically instead of relying on timing luck."""
    store = FirestoreIntentStore()
    store.db = None  # force local (in-memory dict) fallback
    session_id = "race-unlocked"
    store.save_session(session_id, IntentGraph())  # seed an empty session

    barrier = threading.Barrier(2)

    def worker(label: str) -> None:
        graph = store.load_session(session_id)
        barrier.wait()  # guarantee both threads load before either saves
        graph.add_turn(f"turn from {label}", timestamp=0)
        store.save_session(session_id, graph)

    threads = [threading.Thread(target=worker, args=(label,)) for label in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final = store.load_session(session_id)
    assert len(final.nodes) == 1, "expected the race to lose one update - if this fails, the race isn't reproducing"


def test_session_lock_prevents_the_update_loss():
    """Same two-thread race as above, but each thread holds
    session_lock() across its whole load -> mutate -> save sequence -
    the fix actually being tested. Both updates must survive."""
    store = FirestoreIntentStore()
    store.db = None
    session_id = "race-locked"
    store.save_session(session_id, IntentGraph())

    def worker(label: str) -> None:
        with store.session_lock(session_id):
            graph = store.load_session(session_id)
            graph.add_turn(f"turn from {label}", timestamp=0)
            store.save_session(session_id, graph)

    threads = [threading.Thread(target=worker, args=(label,)) for label in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final = store.load_session(session_id)
    assert len(final.nodes) == 2, "both updates should survive when session_lock() is held"
    assert {n.description for n in final.nodes} == {"turn from A", "turn from B"}
