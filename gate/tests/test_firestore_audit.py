"""Tests for gate/firestore_audit.py - specifically that it matches
warden.audit.AuditLogger's REAL log()/read() interface (keyword args:
agent_id/objective/status/tag/note/action/extra), since that's the
interface gate/quorum_gate.py's run_gate() actually calls. A logger with
a different signature (e.g. log(event_type, payload)) breaks on the
first audit.log(...) call inside run_gate() - not a graceful-degradation
case, an immediate TypeError.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gate.firestore_audit import FirestoreAuditLogger


@pytest.fixture
def fallback_root(tmp_path):
    return tmp_path / ".quorum-test"


def test_falls_back_to_disk_when_firestore_unavailable(fallback_root):
    with patch("gate.firestore_audit.FIRESTORE_AVAILABLE", False):
        audit = FirestoreAuditLogger(fallback_root=fallback_root)
        assert audit.db is None

        record = audit.log(
            agent_id="quorum-worker-agent",
            objective="test objective",
            status="sentry:REJECT",
            tag="REJECT",
            note="1 finding",
            extra={"stage": "sentry"},
        )
        assert record["agent_id"] == "quorum-worker-agent"
        assert record["stage"] == "sentry"

        read_back = audit.read(limit=10)
        assert len(read_back) == 1
        assert read_back[0]["objective"] == "test objective"


def test_log_matches_run_gate_call_shape(fallback_root):
    """This is exactly how gate/quorum_gate.py's run_gate() calls
    audit.log() at each of its three stages (Sentry/IntentGraph/Kernel) -
    reproduced here so a signature mismatch fails loudly in this test,
    not silently in a live gate run."""
    with patch("gate.firestore_audit.FIRESTORE_AVAILABLE", False):
        audit = FirestoreAuditLogger(fallback_root=fallback_root)
        audit.log(
            agent_id="quorum-worker-agent",
            objective="task description",
            status="kernel:SUPPORTED",
            tag="SUPPORTED",
            note="",
            extra={"stage": "kernel", "agent_self_report": []},
        )  # raises TypeError here if the signature ever drifts from AuditLogger's real one


def test_writes_to_firestore_when_available(fallback_root):
    mock_doc = MagicMock()
    mock_collection = MagicMock()
    mock_collection.document.return_value = mock_doc
    mock_client = MagicMock()
    mock_client.collection.return_value = mock_collection

    with patch("gate.firestore_audit.FIRESTORE_AVAILABLE", False):
        audit = FirestoreAuditLogger(fallback_root=fallback_root)
    audit.db = mock_client  # simulate a live client without needing real credentials

    record = audit.log(
        agent_id="quorum-worker-agent",
        objective="test objective",
        status="PASS",
        tag="PASS",
        note="",
    )
    mock_collection.document.assert_called_once()
    mock_doc.set.assert_called_once_with(record)


def test_falls_back_to_disk_on_live_firestore_write_failure(fallback_root):
    mock_client = MagicMock()
    mock_client.collection.side_effect = RuntimeError("simulated Firestore outage")

    with patch("gate.firestore_audit.FIRESTORE_AVAILABLE", False):
        audit = FirestoreAuditLogger(fallback_root=fallback_root)
    audit.db = mock_client

    record = audit.log(
        agent_id="quorum-worker-agent",
        objective="test objective",
        status="PASS",
        tag="PASS",
        note="",
    )
    assert record["status"] == "PASS"
    read_back = audit.read(limit=10)
    assert len(read_back) == 1  # landed in the disk fallback despite the simulated outage
