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
