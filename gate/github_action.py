"""Phase 2's terminal action: on a gate PASS, open a real pull request
against the target repo carrying the Worker Agent's proposed diff.

Deliberately git-CLI-based rather than reimplementing multi-file diff
application over GitHub's blob/tree API: `git apply` already handles the
unified-diff format proposal["diff"] is in, correctly, so this only calls
the GitHub REST API for the one thing git itself can't do - creating the
PR object. The token authenticates both the git push (via the remote URL)
and that REST call.

Re-checks verdict == PASS itself, even though callers should already have
gated on it - the entire point of the gate is defeated if this module can
be reached with anything else and silently acts anyway.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Any, Optional

import requests

from gate.quorum_gate import GateResult, GateVerdict
from gate.redaction import scrub_pii

GITHUB_API = "https://api.github.com"


class ActionError(RuntimeError):
    """Raised when the action can't be taken - never swallowed, always
    surfaced to the caller (a 500 in service/main.py, not a silent no-op),
    since a failed action on a PASS verdict is a real problem to know
    about, not something to paper over."""


def open_pr_for_proposal(
    proposal: dict[str, Any],
    gate_result: GateResult,
    *,
    repo: str,
    base_branch: str,
    target_subdir: Optional[str] = None,
    token: Optional[str] = None,
) -> dict[str, str]:
    """Applies proposal["diff"] on a new branch off base_branch, pushes
    it, and opens a PR against base_branch. Returns {"pr_url",
    "commit_sha", "branch"}. Raises ActionError on any failure - verdict
    mismatch, missing token, git failure, or a non-2xx from GitHub.

    proposal["target_files"]/["diff"] are relative to whatever vendored
    component the Worker Agent is scoped to, not the repo root - today
    that's always verifiers/sentry/ (worker_agent/tools.py restricts it
    to that tree only), so callers should pass
    target_subdir="verifiers/sentry" until the Worker Agent's own scope
    changes. Left explicit here rather than hardcoded so that scope
    boundary stays visible at the call site instead of buried in this
    module."""
    if gate_result.verdict != GateVerdict.PASS:
        raise ActionError(
            f"refusing to act on verdict={gate_result.verdict.value!r} - "
            "open_pr_for_proposal only runs on PASS"
        )

    token = token or os.environ.get("QUORUM_ACTION_GITHUB_TOKEN")
    if not token:
        raise ActionError("QUORUM_ACTION_GITHUB_TOKEN not set - cannot authenticate to GitHub")

    diff = proposal.get("diff")
    if not diff:
        raise ActionError("proposal has no 'diff' field to apply")

    safe_stamp = str(proposal.get("generated_at", "unknown")).replace(":", "-").replace("+", "-")
    branch = f"quorum/auto-{safe_stamp}"
    remote_url = f"https://x-access-token:{token}@github.com/{repo}.git"

    with tempfile.TemporaryDirectory() as tmp:
        _run(["git", "clone", "--depth", "1", "--branch", base_branch, remote_url, tmp], redact=token)
        _run(["git", "-C", tmp, "checkout", "-b", branch], redact=token)

        diff_path = os.path.join(tmp, "_quorum_proposal.diff")
        with open(diff_path, "w") as f:
            f.write(diff)
        apply_cmd = ["git", "-C", tmp, "apply", "--whitespace=nowarn"]
        if target_subdir:
            apply_cmd += ["--directory", target_subdir]
        apply_cmd.append("_quorum_proposal.diff")
        try:
            _run(apply_cmd, redact=token)
        finally:
            os.remove(diff_path)

        _run(["git", "-C", tmp, "-c", "user.name=quorum-gate",
              "-c", "user.email=quorum-gate@users.noreply.github.com",
              "add", "-A"], redact=token)
        # Scrubbed (PII shapes) and defanged (@mentions) before it ever
        # becomes a git commit message or PR body/title - both are
        # public, permanent, and (for @mentions) can actually ping real
        # GitHub users. Neither is caught by run_gate()'s Sentry scan,
        # which only inspects diff/rationale as an in-memory proposal,
        # not what this module builds from it afterward.
        safe_task = _defang(scrub_pii(str(proposal.get("task_description", ""))))
        safe_rationale = _defang(scrub_pii(str(proposal.get("rationale", ""))))
        commit_title = f"Quorum auto-PR: {safe_task[:72]}"
        _run(["git", "-C", tmp, "-c", "user.name=quorum-gate",
              "-c", "user.email=quorum-gate@users.noreply.github.com",
              "commit", "-m", commit_title], redact=token)
        commit_sha = _run(["git", "-C", tmp, "rev-parse", "HEAD"], redact=token).strip()
        _run(["git", "-C", tmp, "push", "origin", branch], redact=token)

    pr_body = (
        "Opened automatically by Quorum's coordinator on a gate PASS verdict "
        "- not human-authored.\n\n"
        f"**Task:**\n{safe_task}\n\n"
        f"**Gate verdict:** {gate_result.verdict.value} "
        f"(reasons: {gate_result.reasons or 'none'})\n\n"
        f"**Rationale:**\n{safe_rationale}"
    )
    resp = requests.post(
        f"{GITHUB_API}/repos/{repo}/pulls",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json={
            "title": commit_title,
            "head": branch,
            "base": base_branch,
            "body": pr_body,
        },
        timeout=30,
    )
    if resp.status_code >= 300:
        raise ActionError(_redact(f"GitHub PR creation failed: {resp.status_code} {resp.text}", token))

    pr = resp.json()
    return {"pr_url": pr["html_url"], "commit_sha": commit_sha, "branch": branch}


def _defang(text: str) -> str:
    """Breaks a literal @mention (`@someuser`) so it can't ping a real
    GitHub user or team when this text lands in a PR title/body -
    raw task/rationale text is caller-supplied (or agent-drafted from
    caller-supplied text) and was never meant to be treated as trusted
    GitHub-flavored markdown. A zero-width space after `@` keeps the
    text visually identical while defeating GitHub's mention parser."""
    return text.replace("@", "@​")


def _redact(text: str, secret: Optional[str]) -> str:
    """Scrubs `secret` (the raw GitHub token) out of `text` before it can
    reach an ActionError message. Confirmed live-exploitable: the
    authenticated remote URL (`https://x-access-token:{token}@github.com/...`)
    is passed directly in a subprocess command list, and a failed `git
    clone`/`push`/etc. previously put that raw token straight into
    ActionError's message - which service/main.py returns verbatim as
    `action_error` in the public API response. Every call site in this
    module now passes `redact=token` so this can't regress silently."""
    # A minimum length guard, not just a null check: a trivially short
    # "secret" (a test fixture using token="t", say) would otherwise
    # nuke every occurrence of that character in ordinary text - found
    # exactly that way, via a test using a one-character token that
    # corrupted "GitHub" into "Gi***REDACTED***Hub". Real GitHub tokens
    # are always 40+ characters, so this costs nothing in production.
    if not secret or len(secret) < 8:
        return text
    return text.replace(secret, "***REDACTED***")


def _run(cmd: list[str], *, redact: Optional[str] = None) -> str:
    # subprocess.run raises OSError (FileNotFoundError when the binary
    # itself is missing, PermissionError, etc.) directly from the exec
    # call - that's a different failure mode than a non-zero exit code,
    # and was previously left uncaught here. Confirmed live: on a
    # container without git installed, that let a raw FileNotFoundError
    # escape all the way out to service/main.py's generic exception
    # handler, 500ing the *entire* /gate/retry request and discarding an
    # already-computed PASS verdict, instead of degrading to
    # action_error the way a real ActionError does.
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except OSError as exc:
        raise ActionError(_redact(f"could not run command: {' '.join(cmd)}\n{exc}", redact)) from exc
    except subprocess.TimeoutExpired as exc:
        # Previously unbounded: a stalled network mid-clone/push would
        # hang the request indefinitely instead of failing cleanly, same
        # failure shape the OSError case above was already fixed for.
        raise ActionError(_redact(f"command timed out after 60s: {' '.join(cmd)}", redact)) from exc
    if result.returncode != 0:
        raise ActionError(_redact(f"command failed: {' '.join(cmd)}\n{result.stderr}", redact))
    return result.stdout
