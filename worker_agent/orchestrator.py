"""Runs the Worker Agent end-to-end: draft -> self-check -> at most one
revision -> final self-check -> assembled Proposal.

No verifier is called here. No PR is opened. That happens only after a
PASS verdict from the gate, which doesn't exist yet (Phase 3).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from google.adk.runners import InMemoryRunner

from .agent import MODEL, build_worker_agent
from .schema import DraftProposal, Proposal
from .self_check import run_self_check

APP_NAME = "quorum-worker-agent"
MAX_ATTEMPTS = 2  # one draft + one agent-driven revision, per the Phase 2 brief


class WorkerAgentCallError(RuntimeError):
    """The call to the underlying model (Gemini via ADK/Vertex AI) itself
    failed - a network error, timeout, rate limit, or any other failure
    of the external call. Distinct from the plain RuntimeError below
    (the call succeeded but produced no usable structured draft) and
    from a bug in this project's own code: this failure originates
    outside it. service/main.py maps this to a 502, not a generic 500,
    so a caller can tell "the upstream model call failed, try again"
    apart from "something is actually broken here."
    """


async def _run_worker_agent_async(task_description: str) -> Proposal:
    agent = build_worker_agent()
    runner = InMemoryRunner(agent=agent, app_name=APP_NAME)
    user_id = "quorum"
    session_id = f"worker-{datetime.now(timezone.utc).timestamp()}"
    await runner.session_service.create_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )

    message = task_description
    draft: DraftProposal | None = None
    self_check_result = None
    diff = ""
    revision_notes: str | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            await runner.run_debug(message, user_id=user_id, session_id=session_id, quiet=True)
        except Exception as exc:
            raise WorkerAgentCallError(
                f"Worker Agent's call to Gemini (via ADK) failed on attempt {attempt}: {exc}"
            ) from exc
        session = await runner.session_service.get_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )
        draft_dict = session.state.get("draft") if session else None
        if draft_dict is None:
            raise RuntimeError(
                f"Worker Agent produced no structured draft on attempt {attempt} "
                "(session.state['draft'] is empty)"
            )
        draft = DraftProposal(**draft_dict)

        self_check_result, diff = run_self_check(draft, attempt=attempt)
        if self_check_result.passed:
            break

        if attempt < MAX_ATTEMPTS:
            revision_notes = self_check_result.pytest_summary
            message = (
                "Your draft failed its own self-check. Fix exactly this and "
                "redraft the full structured output (new_rule, tests, "
                "rationale, claims, target_files) - don't restart from "
                "scratch:\n\n" + self_check_result.pytest_summary
            )

    assert draft is not None and self_check_result is not None
    self_check_result = self_check_result.model_copy(update={"revision_notes": revision_notes})

    return Proposal(
        task_description=task_description,
        model=MODEL,
        generated_at=datetime.now(timezone.utc).isoformat(),
        target_files=draft.target_files,
        diff=diff,
        rationale=draft.rationale,
        claims=draft.claims,
        self_check_result=self_check_result,
    )


def run_worker_agent(task_description: str) -> Proposal:
    """Synchronous entry point - what the CLI (and, later, Phase 3) calls."""
    return asyncio.run(_run_worker_agent_async(task_description))
