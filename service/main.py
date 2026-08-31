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
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import gate.quorum_paths  # noqa: F401 - import first, for its sys.path side effects
from gate.agent_identity import API_KEY_HEADER, InvalidAgentKey, resolve_agent_id
from gate.firestore_audit import FirestoreAuditLogger
from gate.firestore_intent import FirestoreIntentStore
from gate.github_action import ActionError, open_pr_for_proposal
from gate.quorum_gate import GateResult, GateVerdict, run_gate
from worker_agent.orchestrator import WorkerAgentCallError

app = FastAPI(title="Quorum Coordinator", version="0.1.0")

_INDEX_HTML = (Path(__file__).parent / "static" / "index.html").read_text()

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
    pr_url: Optional[str] = None
    action_error: Optional[str] = None
    agent_id: Optional[str] = None


def _require_agent_id(x_quorum_agent_key: Optional[str] = Header(None, alias=API_KEY_HEADER)) -> str:
    """FastAPI dependency for the two gate-executing endpoints only - see
    gate/agent_identity.py. Read-only/informational endpoints (/, /api,
    /status, /audit/trail) deliberately do NOT depend on this, so a judge
    or reviewer can still load the service and browse the audit trail
    with no key at all; only actions that actually write a new audit
    entry and can open a real PR require a caller identity.
    """
    try:
        return resolve_agent_id(x_quorum_agent_key)
    except InvalidAgentKey as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _maybe_open_pr(proposal: dict[str, Any], gate_result: GateResult) -> tuple[Optional[str], Optional[str]]:
    """The Phase 2 terminal action: on PASS, and only if the deployment
    has actually configured QUORUM_ACTION_GITHUB_TOKEN, open a real PR
    via gate/github_action.py. Absence of the token means the action is
    off (e.g. running locally) - that's not an error. A configured token
    that then fails IS surfaced via action_error, not swallowed - a
    failed action on a PASS verdict is something the caller needs to
    know about."""
    if gate_result.verdict != GateVerdict.PASS:
        return None, None

    token = os.environ.get("QUORUM_ACTION_GITHUB_TOKEN")
    repo = os.environ.get("QUORUM_ACTION_GITHUB_REPO")
    if not token or not repo:
        return None, None

    base_branch = os.environ.get("QUORUM_ACTION_BASE_BRANCH", "main")
    target_subdir = os.environ.get("QUORUM_ACTION_TARGET_SUBDIR")

    try:
        result = open_pr_for_proposal(
            proposal,
            gate_result,
            repo=repo,
            base_branch=base_branch,
            target_subdir=target_subdir,
            token=token,
        )
        return result["pr_url"], None
    except ActionError as exc:
        return None, str(exc)


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    """Confirmed live: the bare hosted URL (what's actually in Devpost's
    "Hosted project URL" field) 404'd with no route registered for it -
    a judge clicking the link landed on a blank error instead of anything
    useful. Originally this returned raw JSON, which fixed the 404 but
    still wasn't something a judge would want to land on - this serves
    static/index.html instead. The JSON form now lives at GET /api."""
    return _INDEX_HTML


@app.get("/api")
def api_root() -> dict[str, Any]:
    """The same overview as GET /, as JSON - for scripting against."""
    return {
        "service": "Quorum Coordinator",
        "description": "A gated coordinator for an autonomous coding agent - nothing it drafts ships until three independent, deterministic verifiers agree.",
        "endpoints": {
            "GET /status": "health check",
            "POST /gate/run": "evaluate an already-drafted proposal through the gate (no Gemini call) - requires X-Quorum-Agent-Key if agent auth is enforced (see GET /status's agent_auth_enforced)",
            "POST /gate/retry": "draft with the Worker Agent (Gemini 3.5 via Vertex AI) and run it through the gate - same auth requirement as /gate/run",
            "GET /audit/trail": "the append-only Sentry/IntentGraph/Kernel audit log, now keyed by real per-agent identity when auth is enforced",
            "GET /docs": "interactive OpenAPI docs",
        },
        "repo": "https://github.com/Rick-Clinton-jpg/Quorum",
    }


@app.get("/status")
def health_check() -> dict[str, Any]:
    # Not /healthz: confirmed live against the deployed Cloud Run service
    # that Google's platform layer intercepts that exact path on *.run.app
    # before it ever reaches the container (missing "server: Google
    # Frontend" / x-cloud-trace-context headers that every real
    # container-forwarded response carries) - a 404 from something other
    # than this app, not a bug here.
    #
    # github_action_configured is a boolean only - never the secret's
    # actual value - added specifically to diagnose whether a
    # Secret-Manager-backed QUORUM_ACTION_GITHUB_TOKEN is actually
    # readable inside the running container, without needing to guess
    # from a PASS verdict's silently-null pr_url.
    token = os.environ.get("QUORUM_ACTION_GITHUB_TOKEN")
    repo = os.environ.get("QUORUM_ACTION_GITHUB_REPO")
    return {
        "status": "ok",
        "github_action_configured": bool(token) and bool(repo),
        "github_token_length": len(token) if token else 0,
        "github_repo": repo or None,
        # Agent Identity (gate/agent_identity.py): whether POST /gate/run
        # and /gate/retry currently require a valid X-Quorum-Agent-Key
        # header at all - boolean only, never the configured keys.
        "agent_auth_enforced": bool(os.environ.get("QUORUM_AGENT_KEYS")),
    }


@app.post("/gate/retry", response_model=GateResponse)
def execute_retry_gate(req: RetryGateRequest, agent_id: str = Depends(_require_agent_id)) -> GateResponse:
    """Draft with the Worker Agent, run it through the gate, and on
    REJECT redraft once - all inside gate/quorum_gate.py::retry_gate().
    The IntentGraph for `session_id` is loaded before and saved after,
    so re-entry detection actually works across separate calls with the
    same session_id - see gate/firestore_intent.py's docstring for why
    that wasn't true of an earlier draft of this endpoint.

    `agent_id` comes from _require_agent_id (see gate/agent_identity.py)
    - the caller's real identity, resolved from the X-Quorum-Agent-Key
    header, not a hardcoded string - and flows into every audit.log()
    call this request triggers.
    """
    from gate.quorum_gate import retry_gate  # deferred: same reason quorum_gate.py itself defers this import

    try:
        intent_graph = _INTENT_STORE.load_session(req.session_id)

        proposal, gate_result, history = retry_gate(
            task_description=req.task_description,
            max_gate_attempts=req.max_gate_attempts,
            intent_graph=intent_graph,
            audit=_AUDIT,
            agent_id=agent_id,
        )

        _INTENT_STORE.save_session(req.session_id, intent_graph)

        pr_url, action_error = _maybe_open_pr(proposal, gate_result)

        return GateResponse(
            session_id=req.session_id,
            verdict=gate_result.verdict.value,
            reasons=gate_result.reasons,
            proposal=proposal,
            attempts=len(history),
            pr_url=pr_url,
            action_error=action_error,
            agent_id=agent_id,
        )
    except WorkerAgentCallError as exc:
        # The upstream Gemini/Vertex AI call itself failed (timeout, rate
        # limit, transient outage) - distinct from a bug in this project's
        # own code. 502, not 500, so a caller can tell "the model call
        # failed, retry" apart from "something here is actually broken."
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface as a 500 with the real cause, not a bare 500
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/gate/run", response_model=GateResponse)
def execute_run_gate(req: RunGateRequest, agent_id: str = Depends(_require_agent_id)) -> GateResponse:
    """Evaluate an already-drafted proposal through the gate once, no
    Worker Agent call. Mainly for replaying/debugging a specific
    proposal (e.g. the fixture in gate/tests/fixtures/) against a real
    session's IntentGraph state, without spending a Gemini call."""
    try:
        intent_graph = _INTENT_STORE.load_session(req.session_id)

        gate_result = run_gate(req.proposal, intent_graph=intent_graph, audit=_AUDIT, agent_id=agent_id)

        _INTENT_STORE.save_session(req.session_id, intent_graph)

        return GateResponse(
            session_id=req.session_id,
            verdict=gate_result.verdict.value,
            reasons=gate_result.reasons,
            proposal=req.proposal,
            agent_id=agent_id,
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
