"""Tests for worker_agent/self_check.py's failure handling.

Only the timeout failure path is covered here - a real self-check run
needs the actual vendored Sentry tree and a real pytest subprocess,
already exercised end-to-end by the live adversarial test scripts
against the deployed service, not by a fast local unit test.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from worker_agent.schema import DraftProposal, NewRule
from worker_agent.self_check import run_self_check

DRAFT = DraftProposal(
    target_files=["rules/default_rules.json"],
    new_rule=NewRule(
        name="test_rule",
        pattern="(?i)test_pattern",
        severity="LOW",
        description="A test rule.",
    ),
    new_test_positive="test_pattern here",
    new_test_negative="nothing to see here",
    rationale="testing",
    claims=[],
)


def test_a_hung_pytest_run_becomes_a_failed_self_check_not_a_raw_exception():
    """Before this fix, a self-check pytest run that hit its own 120s
    timeout (e.g. a draft introducing an infinite loop in its own test)
    propagated a raw subprocess.TimeoutExpired all the way to a 500,
    instead of degrading to a clean failed self-check the way
    build_patch()'s PatchError already did for a malformed patch."""
    with patch(
        "worker_agent.self_check.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["pytest"], timeout=120),
    ):
        result, diff = run_self_check(DRAFT, attempt=1)

    assert result.passed is False
    assert "timed out" in result.pytest_summary
    assert result.existing_suite_passed is False
    assert result.new_test_passed is False
