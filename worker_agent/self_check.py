"""Applies a DraftProposal to a throwaway copy of Sentry and runs its real
pytest suite - the agent's OWN sanity loop, separate from the deterministic
gate that will exist after Phase 3. Nothing here touches the vendored
verifiers/sentry/ tree in this repo; everything happens in a temp dir.
"""

from __future__ import annotations

import difflib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .schema import DraftProposal, SelfCheckResult

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SENTRY_ROOT = _REPO_ROOT / "verifiers" / "sentry"
_RULES_REL = "rules/default_rules.json"
_TESTS_REL = "tests/test_rules_detection.py"

# The six rule names present in the vendored Sentry repo as of Phase 1
# (docs/INTEGRATION_MAP.md Section 2) - used only to tell "a pre-existing
# rule's own test broke" apart from "the new rule's test broke" in the
# self-check summary below.
_ORIGINAL_RULE_NAMES = {
    "agent_directed_address",
    "hidden_channel_instruction",
    "scope_expansion_phrase",
    "confirmation_bypass_claim",
    "env_exfil_pattern",
    "network_exfil_pattern",
}


class PatchError(ValueError):
    """The draft can't even be applied - a malformed patch, not a failing test."""


def _apply_rules_file(original: str, draft: DraftProposal) -> str:
    rules = json.loads(original)
    names = {r["name"] for r in rules}
    if draft.new_rule.name in names:
        raise PatchError(f"rule name {draft.new_rule.name!r} already exists in default_rules.json")
    try:
        re.compile(draft.new_rule.pattern)
    except re.error as exc:
        raise PatchError(f"new_rule.pattern does not compile as a regex: {exc}") from exc
    if draft.new_rule.severity not in {"HIGH", "MEDIUM", "LOW"}:
        raise PatchError(f"invalid severity {draft.new_rule.severity!r}")

    rules.append(
        {
            "name": draft.new_rule.name,
            "pattern": draft.new_rule.pattern,
            "severity": draft.new_rule.severity,
            "description": draft.new_rule.description,
        }
    )
    # ensure_ascii=False - the file's existing entries contain literal
    # em-dashes; re-serializing with json.dumps' default (ensure_ascii=True)
    # would \u-escape those, turning a one-rule addition into a diff that
    # also touches every unrelated existing description.
    return json.dumps(rules, indent=2, ensure_ascii=False) + "\n"


def _apply_tests_file(original: str, draft: DraftProposal) -> str:
    """Inserts one new CASES entry, matching test_rules_detection.py's own
    dict-literal style, right before the line that closes the dict (a bare
    "}"). Line-based, so the insertion always lands on its own lines."""
    lines = original.splitlines(keepends=True)
    close_idx = next((i for i, line in enumerate(lines) if line.rstrip("\n") == "}"), None)
    if close_idx is None:
        raise PatchError(
            "could not find CASES' closing '}' in test_rules_detection.py - file format changed"
        )
    entry_lines = [
        f'    "{draft.new_rule.name}": {{\n',
        f"        \"positive\": {json.dumps(draft.new_test_positive)},\n",
        f"        \"negative\": {json.dumps(draft.new_test_negative)},\n",
        "    },\n",
    ]
    new_lines = lines[:close_idx] + entry_lines + lines[close_idx:]
    return "".join(new_lines)


def _unified_diff(path: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def build_patch(draft: DraftProposal) -> tuple[dict[str, str], str]:
    """Returns ({sentry-relative path: new file content}, combined unified diff).

    Raises PatchError if the draft can't be applied at all (duplicate rule
    name, invalid regex/severity, or an unrecognized test-file shape).
    """
    rules_before = (_SENTRY_ROOT / _RULES_REL).read_text()
    tests_before = (_SENTRY_ROOT / _TESTS_REL).read_text()

    rules_after = _apply_rules_file(rules_before, draft)
    tests_after = _apply_tests_file(tests_before, draft)

    diff = _unified_diff(_RULES_REL, rules_before, rules_after) + _unified_diff(
        _TESTS_REL, tests_before, tests_after
    )
    return {_RULES_REL: rules_after, _TESTS_REL: tests_after}, diff


def _classify_failures(stdout: str, new_rule_name: str) -> tuple[bool, bool]:
    """From `pytest -v` stdout, returns (existing_suite_passed, new_test_passed).

    A collection-level break (e.g. the new rule's JSON entry makes
    default_rules.json fail to load at all) fails every test, including
    the pre-existing ones - that's reported here as existing_suite_passed
    = False too, which is the honest read: the patch broke rule loading
    for everyone, not just for its own new tests.
    """
    failed_lines = [
        line for line in stdout.splitlines() if "FAILED" in line or "ERROR" in line
    ]
    existing_broke = any(new_rule_name not in line for line in failed_lines)
    new_broke = any(new_rule_name in line for line in failed_lines)
    return (not existing_broke), (not new_broke)


def run_self_check(draft: DraftProposal, attempt: int) -> tuple[SelfCheckResult, str]:
    """Applies the draft to a temp copy of Sentry and runs its pytest suite there.

    Returns (SelfCheckResult, unified diff text). On a PatchError the
    check fails immediately with the error as the summary - no pytest run,
    and the diff is empty since nothing applied cleanly.
    """
    try:
        files, diff = build_patch(draft)
    except PatchError as exc:
        return (
            SelfCheckResult(
                passed=False,
                attempts=attempt,
                existing_suite_passed=False,
                new_test_passed=False,
                pytest_summary=f"patch could not be applied: {exc}",
            ),
            "",
        )

    with tempfile.TemporaryDirectory(prefix="quorum-sentry-selfcheck-") as tmp:
        tmp_sentry = Path(tmp) / "sentry"
        shutil.copytree(_SENTRY_ROOT, tmp_sentry, ignore=shutil.ignore_patterns(".git"))
        for rel_path, content in files.items():
            (tmp_sentry / rel_path).write_text(content)

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-v", "tests"],
                cwd=tmp_sentry,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            # Previously uncaught: a hung/runaway self-check pytest run
            # (e.g. a draft that introduces an infinite loop in its own
            # test) propagated as a raw TimeoutExpired all the way to a
            # 500, instead of degrading to a clean failed self-check the
            # way build_patch()'s PatchError already does above. A
            # timeout IS a self-check failure, not a server error.
            return (
                SelfCheckResult(
                    passed=False,
                    attempts=attempt,
                    existing_suite_passed=False,
                    new_test_passed=False,
                    pytest_summary=f"self-check pytest run timed out after {exc.timeout}s",
                ),
                diff,
            )

    combined = result.stdout + result.stderr
    passed = result.returncode == 0
    existing_suite_passed, new_test_passed = _classify_failures(combined, draft.new_rule.name)
    summary_tail = "\n".join(combined.strip().splitlines()[-40:])

    return (
        SelfCheckResult(
            passed=passed,
            attempts=attempt,
            existing_suite_passed=existing_suite_passed,
            new_test_passed=new_test_passed,
            pytest_summary=summary_tail,
        ),
        diff,
    )
