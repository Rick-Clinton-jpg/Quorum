"""Tests for Agent Identity's HTTP-layer wiring (service/main.py).

Real end-to-end gate behavior is already covered by gate/tests/ - these
tests are specifically about the auth boundary: which endpoints require
X-Quorum-Agent-Key once QUORUM_AGENT_KEYS is set, which stay open, and
that the resolved agent_id actually reaches the response/audit trail.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from gate.agent_identity import API_KEY_HEADER
from gate.quorum_gate import GateResult, GateVerdict
from service.main import app
from worker_agent.orchestrator import WorkerAgentCallError

FIXTURES = Path(__file__).parent.parent.parent / "gate" / "tests" / "fixtures"

client = TestClient(app)


@pytest.fixture
def reject_demo_request() -> dict:
    return json.loads((FIXTURES / "reject_demo_request.json").read_text())


def test_readonly_endpoints_stay_open_with_auth_configured(monkeypatch):
    monkeypatch.setenv("QUORUM_AGENT_KEYS", '{"key-a": "agent-alpha"}')
    assert client.get("/").status_code == 200
    assert client.get("/api").status_code == 200
    assert client.get("/status").status_code == 200
    assert client.get("/audit/trail").status_code == 200


def test_status_reports_agent_auth_enforced(monkeypatch):
    monkeypatch.delenv("QUORUM_AGENT_KEYS", raising=False)
    assert client.get("/status").json()["agent_auth_enforced"] is False

    monkeypatch.setenv("QUORUM_AGENT_KEYS", '{"key-a": "agent-alpha"}')
    assert client.get("/status").json()["agent_auth_enforced"] is True


def test_status_never_reports_token_length():
    """A length is still information about a secret an unauthenticated
    public endpoint has no reason to reveal - dropped entirely, not just
    the value."""
    assert "github_token_length" not in client.get("/status").json()


def test_audit_trail_scrubs_pii_from_objective_field(monkeypatch, reject_demo_request):
    monkeypatch.delenv("QUORUM_AGENT_KEYS", raising=False)
    body = dict(reject_demo_request, session_id="pii-audit-scrub-test")
    body["proposal"] = dict(
        body["proposal"], task_description="Contact jane.doe@example.com re: this diagnosis-pii-scrub probe"
    )
    client.post("/gate/run", json=body)

    trail = client.get("/audit/trail", params={"limit": 500}).json()
    matching = [r for r in trail["records"] if "diagnosis-pii-scrub" in r.get("objective", "")]
    assert matching, "expected at least one record from this probe"
    assert all("jane.doe@example.com" not in r["objective"] for r in matching)


def test_gate_run_open_when_auth_not_configured(monkeypatch, reject_demo_request):
    monkeypatch.delenv("QUORUM_AGENT_KEYS", raising=False)
    resp = client.post("/gate/run", json=reject_demo_request)
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "REJECT"
    assert body["agent_id"] == "quorum-worker-agent"


def test_gate_run_rejects_missing_key_when_auth_configured(monkeypatch, reject_demo_request):
    monkeypatch.setenv("QUORUM_AGENT_KEYS", '{"key-a": "agent-alpha"}')
    resp = client.post("/gate/run", json=reject_demo_request)
    assert resp.status_code == 401


def test_gate_run_rejects_wrong_key_when_auth_configured(monkeypatch, reject_demo_request):
    monkeypatch.setenv("QUORUM_AGENT_KEYS", '{"key-a": "agent-alpha"}')
    resp = client.post(
        "/gate/run",
        json=reject_demo_request,
        headers={API_KEY_HEADER: "not-a-real-key"},
    )
    assert resp.status_code == 401


def test_gate_run_accepts_valid_key_and_reports_resolved_agent_id(monkeypatch, reject_demo_request):
    monkeypatch.setenv("QUORUM_AGENT_KEYS", '{"key-a": "agent-alpha"}')
    resp = client.post(
        "/gate/run",
        json=reject_demo_request,
        headers={API_KEY_HEADER: "key-a"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "REJECT"
    assert body["agent_id"] == "agent-alpha"


def test_gate_run_audit_trail_records_the_resolved_agent_id(monkeypatch, reject_demo_request):
    monkeypatch.setenv("QUORUM_AGENT_KEYS", '{"key-a": "agent-alpha"}')
    session_id = "agent-identity-audit-test"
    body = dict(reject_demo_request, session_id=session_id)
    client.post("/gate/run", json=body, headers={API_KEY_HEADER: "key-a"})

    trail = client.get("/audit/trail", params={"session": "agent-alpha", "limit": 10}).json()
    assert trail["records"], "expected at least one audit record under agent-alpha"
    assert all(r["agent_id"] == "agent-alpha" for r in trail["records"])


def test_gate_retry_maps_worker_agent_call_failure_to_502_not_500(monkeypatch):
    """If the Worker Agent's own call to Gemini fails (timeout, rate
    limit, transient outage), the caller must be able to tell that apart
    from an actual bug in this project's own code - 502, not a generic
    500."""
    monkeypatch.delenv("QUORUM_AGENT_KEYS", raising=False)
    with patch("gate.quorum_gate.retry_gate", side_effect=WorkerAgentCallError("simulated Vertex AI outage")):
        resp = client.post("/gate/retry", json={"task_description": "probe", "session_id": "worker-agent-failure-test"})
    assert resp.status_code == 502
    assert "simulated Vertex AI outage" in resp.json()["detail"]


def test_malformed_nested_claim_returns_422_not_500(monkeypatch):
    """Confirmed missing before this fix: proposal was dict[str, Any],
    so a malformed nested field (here: a claim missing 'confidence' and
    'origin', both required) failed deep inside run_gate()'s
    build_claim_graph() as an unhandled KeyError, surfacing as an opaque
    500 instead of a clean 422 at the request boundary - before Gemini
    or the gate ever ran."""
    monkeypatch.delenv("QUORUM_AGENT_KEYS", raising=False)
    resp = client.post(
        "/gate/run",
        json={
            "proposal": {
                "task_description": "probe",
                "diff": "+placeholder",
                "rationale": "n/a",
                "claims": [{"id": "C0", "statement": "missing required fields"}],
                "target_files": [],
            },
            "session_id": "malformed-claim-test",
        },
    )
    assert resp.status_code == 422


def test_empty_task_description_returns_422():
    resp = client.post("/gate/run", json={"proposal": {"task_description": "", "diff": "", "rationale": "", "claims": [], "target_files": []}})
    assert resp.status_code == 422


def test_zero_max_gate_attempts_returns_422_not_a_crash():
    """Before this fix, max_gate_attempts=0 made retry_gate()'s
    `for attempt in range(1, 1)` loop never execute, leaving
    gate_result as None and failing its own internal assertion - an
    unhandled AssertionError, not a clean validation error."""
    resp = client.post("/gate/retry", json={"task_description": "probe", "max_gate_attempts": 0})
    assert resp.status_code == 422


def test_session_id_with_a_slash_returns_422():
    """session_id ends up as part of a Firestore document path - a
    slash breaks that path outright."""
    resp = client.post("/gate/retry", json={"task_description": "probe", "session_id": "not/a/valid/path"})
    assert resp.status_code == 422


def _fake_pass_result():
    return ({"task_description": "probe", "diff": "+x", "rationale": "r"}, GateResult(verdict=GateVerdict.PASS, reasons=[]), [{"attempt": 1, "gate_verdict": "PASS", "reasons": []}])


def test_idempotency_key_returns_cached_response_without_rerunning_the_workflow(monkeypatch):
    monkeypatch.delenv("QUORUM_AGENT_KEYS", raising=False)
    with patch("gate.quorum_gate.retry_gate") as mock_retry:
        mock_retry.return_value = _fake_pass_result()
        body = {"task_description": "idempotency probe", "session_id": "idem-test-1", "idempotency_key": "key-abc-123"}

        first = client.post("/gate/retry", json=body)
        second = client.post("/gate/retry", json=body)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    mock_retry.assert_called_once()  # the second call must NOT have re-run the workflow


def test_idempotency_key_reused_with_different_payload_returns_409(monkeypatch):
    monkeypatch.delenv("QUORUM_AGENT_KEYS", raising=False)
    with patch("gate.quorum_gate.retry_gate") as mock_retry:
        mock_retry.return_value = _fake_pass_result()
        client.post("/gate/retry", json={"task_description": "first task", "session_id": "idem-test-2", "idempotency_key": "key-xyz-456"})
        resp = client.post("/gate/retry", json={"task_description": "a totally different task", "session_id": "idem-test-2", "idempotency_key": "key-xyz-456"})

    assert resp.status_code == 409


def test_failed_workflow_releases_the_idempotency_key_for_retry(monkeypatch):
    """A failed attempt must not permanently strand the key as PENDING -
    a real retry needs to be able to succeed."""
    monkeypatch.delenv("QUORUM_AGENT_KEYS", raising=False)
    with patch("gate.quorum_gate.retry_gate") as mock_retry:
        mock_retry.side_effect = [RuntimeError("simulated failure"), _fake_pass_result()]
        body = {"task_description": "probe", "session_id": "idem-test-3", "idempotency_key": "key-retry-789"}

        first = client.post("/gate/retry", json=body)
        second = client.post("/gate/retry", json=body)

    assert first.status_code == 500
    assert second.status_code == 200
    assert mock_retry.call_count == 2
