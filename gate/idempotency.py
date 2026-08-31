"""Atomic idempotency for POST /gate/retry.

A client retry (a network timeout on their end, even though the server
actually completed) or an ambiguous GitHub timeout (the push/PR may have
succeeded remotely but timed out locally) must not silently duplicate a
Gemini call, a git push, or a PR.

"Check a store, then run" - the naive approach - has the exact same
lost-update race gate/firestore_intent.py's session_lock() was built to
close: two concurrent requests with the same key could both pass the
check before either finishes claiming it. The fix here uses the atomic
primitive Firestore already provides for free: a document CREATE that
fails if the key already exists, used as the claim step, not a
read-then-write.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    from google.cloud import firestore

    FIRESTORE_AVAILABLE = True
except ImportError:
    FIRESTORE_AVAILABLE = False


class IdempotencyKeyReused(Exception):
    """The same key was submitted with a different request payload -
    refused outright, never silently allowed to overwrite what the key
    already means. Without this check, an idempotency key could be
    (accidentally or deliberately) reused to swap in a different task
    and get back a cached result for something that was never actually
    run with those inputs."""


class IdempotencyInProgress(Exception):
    """Another request with the same key claimed PENDING and hasn't
    completed yet."""


def _payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class IdempotencyStore:
    """One PENDING/COMPLETED record per idempotency key. Falls back to
    an in-memory dict (per-process only, same disclosed single-instance
    limitation as gate/firestore_intent.py's local fallback) when
    Firestore is unavailable or a live call fails."""

    def __init__(self, collection_name: str = "quorum_idempotency", project: Optional[str] = None):
        self.collection_name = collection_name
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.db = None
        self._local: dict[str, dict[str, Any]] = {}
        self._local_guard = threading.Lock()

        if FIRESTORE_AVAILABLE:
            try:
                self.db = firestore.Client(project=self.project)
            except Exception as exc:  # noqa: BLE001 - any init failure means "use local fallback"
                logger.warning("Firestore client init failed for IdempotencyStore (%s) - using local memory.", exc)

    def claim(self, key: str, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Attempts to atomically claim `key` as PENDING for `payload`.

        Returns None if the claim succeeded - the caller should run the
        workflow and call complete(). Returns the cached response dict
        if a prior COMPLETED run with the IDENTICAL payload already
        exists - the caller should return that instead of running
        again. Raises IdempotencyKeyReused if `key` was already claimed
        for a DIFFERENT payload. Raises IdempotencyInProgress if another
        request with this key is still PENDING.
        """
        payload_hash = _payload_hash(payload)

        if self.db is not None:
            try:
                doc_ref = self.db.collection(self.collection_name).document(key)
                try:
                    doc_ref.create({"payload_hash": payload_hash, "status": "PENDING", "response": None})
                    return None
                except Exception:  # noqa: BLE001 - create() failing IS the atomic "already claimed" signal
                    existing = doc_ref.get()
                    if not existing.exists:
                        raise
                    data = existing.to_dict() or {}
                    if data.get("payload_hash") != payload_hash:
                        raise IdempotencyKeyReused(
                            f"idempotency key {key!r} was already used for a different request"
                        )
                    if data.get("status") == "PENDING":
                        raise IdempotencyInProgress(f"a request with idempotency key {key!r} is still in progress")
                    return data.get("response")
            except (IdempotencyKeyReused, IdempotencyInProgress):
                raise
            except Exception as exc:  # noqa: BLE001
                logger.error("Idempotency claim for %r failed against Firestore (%s) - using local fallback.", key, exc)

        with self._local_guard:
            existing_local = self._local.get(key)
            if existing_local is None:
                self._local[key] = {"payload_hash": payload_hash, "status": "PENDING", "response": None}
                return None
            if existing_local.get("payload_hash") != payload_hash:
                raise IdempotencyKeyReused(f"idempotency key {key!r} was already used for a different request")
            if existing_local.get("status") == "PENDING":
                raise IdempotencyInProgress(f"a request with idempotency key {key!r} is still in progress")
            return existing_local.get("response")

    def complete(self, key: str, response: dict[str, Any]) -> None:
        """Marks `key` COMPLETED with `response`, so a later claim() for
        the same key/payload returns this cached response instead of
        running again."""
        if self.db is not None:
            try:
                self.db.collection(self.collection_name).document(key).set(
                    {"status": "COMPLETED", "response": response}, merge=True
                )
                return
            except Exception as exc:  # noqa: BLE001
                logger.error("Idempotency complete() for %r failed against Firestore (%s) - using local fallback.", key, exc)

        with self._local_guard:
            if key in self._local:
                self._local[key]["status"] = "COMPLETED"
                self._local[key]["response"] = response

    def release(self, key: str) -> None:
        """Releases a PENDING claim without marking it COMPLETED - used
        when the workflow itself failed, so the key can be retried
        instead of being stuck PENDING forever."""
        if self.db is not None:
            try:
                self.db.collection(self.collection_name).document(key).delete()
                return
            except Exception as exc:  # noqa: BLE001
                logger.error("Idempotency release() for %r failed against Firestore (%s) - using local fallback.", key, exc)

        with self._local_guard:
            self._local.pop(key, None)
