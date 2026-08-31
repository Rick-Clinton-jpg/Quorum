"""Tests for worker_agent/orchestrator.py's failure handling.

Only the failure path is covered here - a real end-to-end draft needs a
live Gemini/Vertex AI call, exercised instead by the live adversarial
test scripts (competition_tests.sh, full_diagnosis.sh) against the
deployed service, not by a fast local unit test.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from google.adk.runners import InMemoryRunner

from worker_agent import orchestrator
from worker_agent.orchestrator import WorkerAgentCallError, run_worker_agent


def test_model_call_failure_raises_worker_agent_call_error_not_a_raw_exception():
    """Before this fix, any failure of the actual Gemini/ADK call
    (timeout, rate limit, transient outage) propagated as whatever raw
    exception the underlying library happened to raise, indistinguishable
    from a bug in this project's own code. It must now raise the typed
    WorkerAgentCallError instead, so callers (service/main.py) can map it
    to a 502, not a generic 500."""
    with patch.object(
        InMemoryRunner, "run_debug", new=AsyncMock(side_effect=ConnectionError("simulated Vertex AI outage"))
    ):
        with pytest.raises(WorkerAgentCallError) as exc_info:
            run_worker_agent("a task that will never reach the model")

    assert "simulated Vertex AI outage" in str(exc_info.value)
    assert exc_info.value.__cause__ is not None  # the original exception is chained, not swallowed


def test_model_call_that_hangs_times_out_instead_of_blocking_forever():
    """A distinct failure mode from the one above: Gemini/ADK never
    returning at all, not returning an error. Confirmed missing by an
    external parallel review - the earlier fix's try/except only caught
    the call *raising*, never the call simply hanging. Uses a real
    asyncio.wait_for against a coroutine that actually sleeps past a
    shortened timeout (patched to 0.05s so the test stays fast), not a
    mocked timeout, so a regression in the actual asyncio.wait_for
    wiring can't hide behind a mock."""

    async def _hangs_forever(*_args, **_kwargs):
        await asyncio.sleep(10)

    with patch.object(InMemoryRunner, "run_debug", new=_hangs_forever), \
         patch.object(orchestrator, "CALL_TIMEOUT_SECONDS", 0.05):
        with pytest.raises(WorkerAgentCallError, match="exceeded"):
            run_worker_agent("a task whose model call will hang")
