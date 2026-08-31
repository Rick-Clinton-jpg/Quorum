"""Tests for worker_agent/orchestrator.py's failure handling.

Only the failure path is covered here - a real end-to-end draft needs a
live Gemini/Vertex AI call, exercised instead by the live adversarial
test scripts (competition_tests.sh, full_diagnosis.sh) against the
deployed service, not by a fast local unit test.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from google.adk.runners import InMemoryRunner

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
