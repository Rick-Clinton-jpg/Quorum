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
    _estimate_bytes,
    _prune_for_firestore,
    deserialize_intent_graph,
    merge_intent_graphs,
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


def test_prune_returns_unchanged_when_already_under_budget():
    graph = IntentGraph()
    graph.add_turn("small graph", timestamp=0)
    data = serialize_intent_graph(graph)

    pruned, dropped = _prune_for_firestore(data, max_bytes=10_000_000)
    assert dropped == 0
    assert pruned == data


def test_prune_drops_oldest_non_boundary_nodes_first():
    graph = IntentGraph()
    for i in range(6):
        graph.add_turn(f"ordinary turn number {i}", timestamp=i)
    data = serialize_intent_graph(graph)
    original_ids = [n["intent_id"] for n in data["nodes"]]
    full_size = _estimate_bytes(data)

    max_bytes = full_size // 2  # forces pruning, but not everything
    pruned, dropped = _prune_for_firestore(data, max_bytes=max_bytes)

    assert dropped > 0
    assert _estimate_bytes(pruned) <= max_bytes
    remaining_ids = [n["intent_id"] for n in pruned["nodes"]]
    # Whichever ids remain must be a SUFFIX of the original append
    # order (oldest dropped first), not an arbitrary subset.
    assert remaining_ids == original_ids[len(original_ids) - len(remaining_ids):]

    dropped_ids = set(original_ids) - set(remaining_ids)
    for edge in pruned["edges"]:
        assert edge["source"] not in dropped_ids
        assert edge["target"] not in dropped_ids


def test_prune_never_drops_a_safety_boundary_node_even_when_it_is_the_oldest():
    graph = IntentGraph()
    graph.add_turn("ordinary turn 0", timestamp=0)
    graph.add_turn("[SAFETY BOUNDARY TRIGGERED]", timestamp=1)  # flags node 0
    for i in range(2, 6):
        graph.add_turn(f"ordinary turn {i}", timestamp=i)
    boundary_id = graph.nodes[0].intent_id
    assert graph.nodes[0].safety_boundary is True

    data = serialize_intent_graph(graph)
    # A budget far too small to keep anything, if the boundary node
    # weren't specifically protected.
    pruned, dropped = _prune_for_firestore(data, max_bytes=1)

    remaining_ids = {n["intent_id"] for n in pruned["nodes"]}
    assert boundary_id in remaining_ids
    assert dropped == len(graph.nodes) - 1


def test_prune_returns_unchanged_when_every_node_is_a_safety_boundary():
    graph = IntentGraph()
    graph.add_turn("first flagged turn", timestamp=0)
    graph.add_turn("[SAFETY BOUNDARY TRIGGERED]", timestamp=1)
    graph.add_turn("second flagged turn", timestamp=2)
    graph.add_turn("[SAFETY BOUNDARY TRIGGERED]", timestamp=3)
    data = serialize_intent_graph(graph)
    assert data["nodes"] and all(n["safety_boundary"] for n in data["nodes"])

    pruned, dropped = _prune_for_firestore(data, max_bytes=1)
    assert dropped == 0
    assert pruned == data  # nothing safe to drop - left for the write itself to fail loudly


def test_save_session_prunes_before_writing_to_firestore_when_oversized(monkeypatch):
    graph = IntentGraph()
    for i in range(6):
        graph.add_turn(f"ordinary turn {i}", timestamp=i)

    mock_doc_ref = MagicMock()
    mock_collection = MagicMock()
    mock_collection.document.return_value = mock_doc_ref
    mock_client = MagicMock()
    mock_client.collection.return_value = mock_collection

    with patch("gate.firestore_intent.FIRESTORE_AVAILABLE", False):
        store = FirestoreIntentStore()
    store.db = mock_client

    full_size = _estimate_bytes(serialize_intent_graph(graph))
    monkeypatch.setattr("gate.firestore_intent._FIRESTORE_SAFE_BYTES", full_size // 2)

    store.save_session("sess-prune", graph)

    written = mock_doc_ref.set.call_args[0][0]
    assert len(written["nodes"]) < len(graph.nodes), "expected the Firestore write to be pruned"
    assert len(graph.nodes) == 6, "the caller's own in-memory graph must never be mutated by pruning"


def test_merge_unions_nodes_added_locally_during_an_outage():
    """Simulates the real scenario merge_intent_graphs() exists for: one
    continuous process's graph grows across a Firestore outage.
    `stale_firestore_copy` is what Firestore last saw (a real snapshot
    of the SAME graph, taken before the outage) - not an unrelated
    graph. A genuine cross-instance id collision (two different turns
    minted under the same id) is a separate case - see
    test_merge_keeps_both_nodes_on_a_genuine_id_collision below."""
    grown_locally = IntentGraph()
    grown_locally.add_turn("turn 0, saved before the outage", timestamp=0)
    stale_firestore_copy = deserialize_intent_graph(serialize_intent_graph(grown_locally))
    grown_locally.add_turn("turn 1, added during the outage", timestamp=1)
    grown_locally.add_turn("turn 2, added during the outage", timestamp=2)

    merged = merge_intent_graphs(stale_firestore_copy, grown_locally)

    assert {n.description for n in merged.nodes} == {
        "turn 0, saved before the outage",
        "turn 1, added during the outage",
        "turn 2, added during the outage",
    }


def test_merge_next_id_avoids_colliding_with_ids_minted_during_the_outage():
    grown_locally = IntentGraph()
    grown_locally.add_turn("turn 0", timestamp=0)
    stale_firestore_copy = deserialize_intent_graph(serialize_intent_graph(grown_locally))
    grown_locally.add_turn("turn 1", timestamp=1)
    grown_locally.add_turn("turn 2", timestamp=2)  # local _next_id is now 3, Firestore's copy thinks it's 1

    merged = merge_intent_graphs(stale_firestore_copy, grown_locally)
    assert merged._next_id == 3

    existing_ids = {n.intent_id for n in merged.nodes}
    new_node = merged.add_turn("a turn added after the merge", timestamp=99)
    assert new_node.intent_id not in existing_ids


def test_merge_keeps_both_nodes_on_a_genuine_id_collision():
    """Confirmed as a real gap by independent re-audit: session_lock()
    only closes the load-mutate-save race within one process (see its
    own docstring) - two different Cloud Run instances racing on the
    same session_id can each mint a node under the SAME id for two
    ACTUALLY DIFFERENT turns. The previous merge silently let `primary`
    win on any id collision, discarding a real, distinct turn with no
    trace. Both must survive the merge now, even though they share an
    id going in."""
    shared_history = IntentGraph()
    shared_history.add_turn("shared turn 0, seen by both instances", timestamp=0)

    instance_a = deserialize_intent_graph(serialize_intent_graph(shared_history))
    instance_a.add_turn("instance A's own turn 1", timestamp=1)

    instance_b = deserialize_intent_graph(serialize_intent_graph(shared_history))
    instance_b.add_turn("instance B's own, DIFFERENT turn 1", timestamp=1)

    # Both instances minted their new node as "n1" - a real id collision,
    # not a duplicate: the content genuinely differs.
    assert instance_a.nodes[-1].intent_id == instance_b.nodes[-1].intent_id == "n1"
    assert instance_a.nodes[-1].description != instance_b.nodes[-1].description

    merged = merge_intent_graphs(instance_a, instance_b)

    descriptions = {n.description for n in merged.nodes}
    assert "instance A's own turn 1" in descriptions
    assert "instance B's own, DIFFERENT turn 1" in descriptions
    # No two surviving nodes share an id post-merge.
    assert len({n.intent_id for n in merged.nodes}) == len(merged.nodes)


def test_merge_deduplicates_a_genuinely_identical_id_collision():
    """The common case - the id collision the OLD code assumed was
    always true - must still just dedupe cheaply, not spuriously fork
    into two copies of the same turn."""
    graph = IntentGraph()
    graph.add_turn("turn 0", timestamp=0)
    same_snapshot_twice = deserialize_intent_graph(serialize_intent_graph(graph))

    merged = merge_intent_graphs(graph, same_snapshot_twice)
    assert len(merged.nodes) == 1


def test_load_session_merges_firestore_and_local_fallback_honestly_and_reconciles():
    """The full path through FirestoreIntentStore: a session that made
    it partway to Firestore before an outage, then grew further in this
    process's own local fallback - load_session() must return the
    union, not just whichever source it happened to check first, and
    must clear the fallback afterward so a second load doesn't re-merge
    the same turns again."""
    grown_locally = IntentGraph()
    grown_locally.add_turn("turn 0, made it to Firestore", timestamp=0)
    firestore_payload = serialize_intent_graph(grown_locally)
    grown_locally.add_turn("turn 1, only in the local fallback", timestamp=1)
    local_payload = serialize_intent_graph(grown_locally)

    mock_doc_snapshot = MagicMock(exists=True)
    mock_doc_snapshot.to_dict.return_value = firestore_payload
    mock_doc_ref = MagicMock()
    mock_doc_ref.get.return_value = mock_doc_snapshot
    mock_collection = MagicMock()
    mock_collection.document.return_value = mock_doc_ref
    mock_client = MagicMock()
    mock_client.collection.return_value = mock_collection

    with patch("gate.firestore_intent.FIRESTORE_AVAILABLE", False):
        store = FirestoreIntentStore()
    store.db = mock_client
    store._local_sessions["sess-merge"] = local_payload

    merged = store.load_session("sess-merge")
    assert {n.description for n in merged.nodes} == {
        "turn 0, made it to Firestore",
        "turn 1, only in the local fallback",
    }

    assert "sess-merge" not in store._local_sessions, "expected the fallback to be reconciled, not re-merged forever"


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
