"""Tests for gate/idempotency.py's IdempotencyStore.

The critical case isn't "does caching work" - it's "is the claim step
actually atomic." A naive check-then-set has the identical lost-update
race gate/firestore_intent.py's session_lock() was built to close for
IntentGraph sessions; this module exists so idempotency doesn't
reintroduce that same bug in a new place.
"""

from __future__ import annotations

import threading

import pytest

from gate.idempotency import IdempotencyInProgress, IdempotencyKeyReused, IdempotencyStore


def test_first_claim_succeeds_and_returns_none():
    store = IdempotencyStore()
    store.db = None  # force local fallback
    assert store.claim("key-1", {"task": "a"}) is None


def test_second_claim_with_same_key_and_payload_while_pending_raises_in_progress():
    store = IdempotencyStore()
    store.db = None
    store.claim("key-1", {"task": "a"})
    with pytest.raises(IdempotencyInProgress):
        store.claim("key-1", {"task": "a"})


def test_claim_with_same_key_but_different_payload_is_refused():
    """Prevents an idempotency key from being reused to swap in a
    different task and silently get back an unrelated cached result."""
    store = IdempotencyStore()
    store.db = None
    store.claim("key-1", {"task": "a"})
    with pytest.raises(IdempotencyKeyReused):
        store.claim("key-1", {"task": "b"})


def test_completed_claim_returns_the_cached_response_on_retry():
    store = IdempotencyStore()
    store.db = None
    store.claim("key-1", {"task": "a"})
    store.complete("key-1", {"verdict": "PASS", "pr_url": "https://example.com/pr/1"})

    cached = store.claim("key-1", {"task": "a"})
    assert cached == {"verdict": "PASS", "pr_url": "https://example.com/pr/1"}


def test_release_unsticks_a_pending_claim_for_retry():
    """A workflow that itself fails (not the action, the whole attempt)
    must not permanently strand the key as PENDING forever - the client
    needs to be able to retry."""
    store = IdempotencyStore()
    store.db = None
    store.claim("key-1", {"task": "a"})
    store.release("key-1")
    assert store.claim("key-1", {"task": "a"}) is None  # claimable again


def test_concurrent_claims_for_the_same_key_only_let_one_through():
    """Forces the actual race, not just sequential calls: two threads
    claiming the identical key/payload at the same instant - the naive
    check-then-set shape would let both through."""
    store = IdempotencyStore()
    store.db = None
    barrier = threading.Barrier(2)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            result = store.claim("race-key", {"task": "a"})
            with lock:
                results.append(("claimed", result))
        except IdempotencyInProgress:
            with lock:
                results.append(("in_progress", None))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    claimed = [r for r in results if r[0] == "claimed"]
    in_progress = [r for r in results if r[0] == "in_progress"]
    assert len(claimed) == 1, f"expected exactly one successful claim, got {results}"
    assert len(in_progress) == 1
