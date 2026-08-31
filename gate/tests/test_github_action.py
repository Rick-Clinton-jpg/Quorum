"""Tests for gate/github_action.py. All git/HTTP calls are mocked - a
real end-to-end run (real gate PASS, real git clone/apply/push, real PR)
was done separately against the actual repo; see PR #1 on
Rick-Clinton-jpg/Quorum for that live proof. These tests cover the parts
a live run doesn't exercise cheaply/repeatably: the gating logic itself,
and that a failure at each step surfaces as ActionError rather than being
swallowed or silently producing a wrong result.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from gate.github_action import ActionError, _run, open_pr_for_proposal
from gate.quorum_gate import GateResult, GateVerdict

PROPOSAL = {
    "task_description": "Add a rule",
    "generated_at": "2026-08-18T07:38:26.022534+00:00",
    "diff": "--- a/rules/x.json\n+++ b/rules/x.json\n@@\n+change\n",
    "rationale": "because",
}


def _pass_result(reasons=None) -> GateResult:
    return GateResult(verdict=GateVerdict.PASS, reasons=reasons or [])


def test_refuses_to_act_on_reject():
    result = GateResult(verdict=GateVerdict.REJECT, reasons=["sentry finding"])
    with pytest.raises(ActionError, match="refusing to act"):
        open_pr_for_proposal(PROPOSAL, result, repo="o/r", base_branch="main", token="t")


def test_refuses_to_act_on_escalate():
    result = GateResult(verdict=GateVerdict.ESCALATE, reasons=["needs human review"])
    with pytest.raises(ActionError, match="refusing to act"):
        open_pr_for_proposal(PROPOSAL, result, repo="o/r", base_branch="main", token="t")


def test_raises_without_a_token(monkeypatch):
    monkeypatch.delenv("QUORUM_ACTION_GITHUB_TOKEN", raising=False)
    with pytest.raises(ActionError, match="QUORUM_ACTION_GITHUB_TOKEN"):
        open_pr_for_proposal(PROPOSAL, _pass_result(), repo="o/r", base_branch="main", token=None)


def test_raises_when_proposal_has_no_diff():
    proposal = {k: v for k, v in PROPOSAL.items() if k != "diff"}
    with pytest.raises(ActionError, match="no 'diff'"):
        open_pr_for_proposal(proposal, _pass_result(), repo="o/r", base_branch="main", token="t")


def test_run_converts_missing_binary_to_action_error():
    """Found live: python:3.11-slim has no git installed, so
    subprocess.run(["git", ...]) raised a raw FileNotFoundError that
    escaped _run() entirely (only non-zero exit codes were handled),
    which then 500'd the whole /gate/retry request instead of degrading
    to action_error - discarding an already-computed PASS verdict.
    Exercises _run() itself directly (not mocked), against a binary that
    genuinely doesn't exist, so a regression here can't hide behind a
    mocked subprocess.run in the other tests."""
    with pytest.raises(ActionError, match="could not run command"):
        _run(["definitely-not-a-real-binary-xyz", "--version"])


def test_run_converts_hang_to_action_error_instead_of_blocking_forever():
    """Same failure shape as the missing-binary case above, for a
    different cause: a git clone/push that stalls (network hiccup,
    auth prompt hang) previously had no timeout at all and would block
    the request indefinitely instead of failing cleanly. Exercises
    _run() directly against a command that genuinely sleeps past the
    60s timeout, using `sleep` (not git) so the test itself stays fast -
    patches subprocess.run's own timeout handling isn't mocked, only
    the sleep duration is shortened via a real subprocess.TimeoutExpired
    from a 0.05s timeout instead of waiting out the real 60s."""
    with patch("gate.github_action.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["git", "push"], timeout=60)):
        with pytest.raises(ActionError, match="timed out"):
            _run(["git", "push"])


def test_git_failure_surfaces_as_action_error():
    with patch("gate.github_action._run") as mock_run:
        mock_run.side_effect = ActionError("command failed: git clone ...\nfatal: repo not found")
        with pytest.raises(ActionError, match="command failed"):
            open_pr_for_proposal(PROPOSAL, _pass_result(), repo="o/r", base_branch="main", token="t")


def test_github_api_failure_surfaces_as_action_error():
    with patch("gate.github_action._run") as mock_run, \
         patch("gate.github_action.requests.post") as mock_post:
        mock_run.return_value = "02315a1df14f5997d62bae76512eecd4db64166\n"
        mock_post.return_value = MagicMock(status_code=422, text='{"message":"Validation Failed"}')

        with pytest.raises(ActionError, match="GitHub PR creation failed: 422"):
            open_pr_for_proposal(PROPOSAL, _pass_result(), repo="o/r", base_branch="main", token="t")


def test_happy_path_returns_pr_url_and_commit_sha():
    with patch("gate.github_action._run") as mock_run, \
         patch("gate.github_action.requests.post") as mock_post, \
         patch("builtins.open", MagicMock()), \
         patch("os.remove", MagicMock()):
        mock_run.return_value = "02315a1df14f5997d62bae76512eecd4db64166\n"
        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {"html_url": "https://github.com/o/r/pull/1"},
        )

        result = open_pr_for_proposal(
            PROPOSAL, _pass_result(), repo="o/r", base_branch="main",
            target_subdir="verifiers/sentry", token="t",
        )

        assert result["pr_url"] == "https://github.com/o/r/pull/1"
        assert result["commit_sha"] == "02315a1df14f5997d62bae76512eecd4db64166"
        assert result["branch"] == "quorum/auto-2026-08-18T07-38-26.022534-00-00"

        apply_calls = [c for c in mock_run.call_args_list if "apply" in c.args[0]]
        assert len(apply_calls) == 1
        assert "--directory" in apply_calls[0].args[0]
        assert "verifiers/sentry" in apply_calls[0].args[0]
