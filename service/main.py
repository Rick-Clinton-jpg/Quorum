"""Quorum coordinator HTTP service - the Cloud Run entry point.

Wraps gate/quorum_gate.py's run_gate()/retry_gate() behind two endpoints,
with real session persistence via gate/firestore_intent.py and
gate/firestore_audit.py (both fall back to local storage automatically
if Firestore isn't configured - see those modules' docstrings - so this
runs locally with zero GCP setup, same behavior either way except where
state is actually kept).

Not deployed yet. Deploying is a deliberate later step, once the
hackathon's GCP credit lands - see service/README.md for exactly what
that step is and why it's separate from everything in this file.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Optional

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import gate.quorum_paths  # noqa: F401 - import first, for its sys.path side effects
from gate.firestore_audit import FirestoreAuditLogger
from gate.firestore_intent import FirestoreIntentStore
from gate.quorum_gate import run_gate

app = FastAPI(title="Quorum Coordinator", version="0.1.0")

_AUDIT = FirestoreAuditLogger(
    collection_name="quorum_audit_logs",
    fallback_root=os.path.join(REPO_ROOT, ".quorum"),
)
_INTENT_STORE = FirestoreIntentStore(collection_name="quorum_intent_sessions")


class RetryGateRequest(BaseModel):
    task_description: str
    max_gate_attempts: int = 2
    session_id: str = "default-session"


class RunGateRequest(BaseModel):
    proposal: dict[str, Any]
    session_id: str = "default-session"


class GateResponse(BaseModel):
    session_id: str
    verdict: str
    reasons: list[str]
    proposal: Optional[dict[str, Any]] = None
    attempts: Optional[int] = None


@app.get("/healthz")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/gate/retry", response_model=GateResponse)
def execute_retry_gate(req: RetryGateRequest) -> GateResponse:
    """Draft with the Worker Agent, run it through the gate, and on
    REJECT redraft once - all inside gate/quorum_gate.py::retry_gate().
    The IntentGraph for `session_id` is loaded before and saved after,
    so re-entry detection actually works across separate calls with the
    same session_id - see gate/firestore_intent.py's docstring for why
    that wasn't true of an earlier draft of this endpoint.
    """
    from gate.quorum_gate import retry_gate  # deferred: same reason quorum_gate.py itself defers this import

    try:
        intent_graph = _INTENT_STORE.load_session(req.session_id)

        proposal, gate_result, history = retry_gate(
            task_description=req.task_description,
            max_gate_attempts=req.max_gate_attempts,
            intent_graph=intent_graph,
            audit=_AUDIT,
        )

        _INTENT_STORE.save_session(req.session_id, intent_graph)

        return GateResponse(
            session_id=req.session_id,
            verdict=gate_result.verdict.value,
            reasons=gate_result.reasons,
            proposal=proposal,
            attempts=len(history),
        )
    except Exception as exc:  # noqa: BLE001 - surface as a 500 with the real cause, not a bare 500
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/gate/run", response_model=GateResponse)
def execute_run_gate(req: RunGateRequest) -> GateResponse:
    """Evaluate an already-drafted proposal through the gate once, no
    Worker Agent call. Mainly for replaying/debugging a specific
    proposal (e.g. the fixture in gate/tests/fixtures/) against a real
    session's IntentGraph state, without spending a Gemini call."""
    try:
        intent_graph = _INTENT_STORE.load_session(req.session_id)

        gate_result = run_gate(req.proposal, intent_graph=intent_graph, audit=_AUDIT)

        _INTENT_STORE.save_session(req.session_id, intent_graph)

        return GateResponse(
            session_id=req.session_id,
            verdict=gate_result.verdict.value,
            reasons=gate_result.reasons,
            proposal=req.proposal,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/audit/trail")
def get_audit_trail(session: Optional[str] = None, tag: Optional[str] = None, limit: int = 50) -> dict[str, Any]:
    """Browse the append-only audit trail. `session` filters on the
    agent_id field AuditLogger.log() actually writes (see
    verifiers/warden/warden/audit.py) - it is not a Firestore-session
    identifier, despite the name; that's Warden's own field name, kept
    as-is since this reuses Warden's real interface."""
    try:
        return {"records": _AUDIT.read(session=session, tag=tag, limit=limit)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("service.main:app", host="0.0.0.0", port=port)
