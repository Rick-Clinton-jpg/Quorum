"""A malicious or merely careless agent-drafted regex must not be able to
hang scan() - rules/default_rules.json is exactly the file the Worker
Agent proposes diffs to (see gate/tests/fixtures/escalate_demo_proposal.json),
so a rule's pattern is untrusted input, not maintainer-reviewed. Uses a
real catastrophic-backtracking pattern against real input - not a mocked
timeout - to prove the timeout= wiring actually bounds the match, not
just that the code compiles.
"""

from __future__ import annotations

import time

import regex

from sentry.engine import Rule, scan

# Classic ambiguous-alternation ReDoS shape: for a run of N "a"s with no
# trailing "b", the engine explores exponentially many ways to split the
# run between the two alternatives before giving up. Confirmed locally to
# still be running well past a 1s budget at this length, uncapped.
_CATASTROPHIC_PATTERN = r"(a|aa)+b"
_CATASTROPHIC_RULE = Rule(
    name="malicious_agent_drafted_rule",
    pattern=_CATASTROPHIC_PATTERN,
    severity="LOW",
    description="a rule an agent could plausibly draft without realizing it's catastrophic",
    regex=regex.compile(_CATASTROPHIC_PATTERN),
)


def test_a_catastrophic_backtracking_rule_does_not_hang_scan():
    text = "a" * 40  # no trailing "b" - forces the runaway backtrack search
    start = time.monotonic()
    matches = scan(text, [_CATASTROPHIC_RULE])
    elapsed = time.monotonic() - start

    assert elapsed < 5.0, f"scan() took {elapsed:.2f}s - the timeout did not bound the runaway match"
    assert any(m.rule == "malicious_agent_drafted_rule" for m in matches)


def test_a_catastrophic_backtracking_rule_is_reported_as_a_high_severity_finding():
    """Fail closed, not open: a rule that can't be safely evaluated is
    itself the finding, forced to HIGH regardless of what severity the
    rule declared for itself - "unsafe to evaluate" must never quietly
    downgrade to the rule's own (possibly LOW) severity."""
    matches = scan("a" * 40, [_CATASTROPHIC_RULE])
    timeout_matches = [m for m in matches if m.rule == "malicious_agent_drafted_rule"]

    assert timeout_matches, "expected the timed-out rule to surface as a finding, not be silently dropped"
    assert all(m.severity == "HIGH" for m in timeout_matches)


def test_a_normal_rule_alongside_a_catastrophic_one_still_gets_evaluated():
    """One unsafe rule must not take down the rest of the ruleset."""
    normal_rule = Rule(
        name="normal_rule",
        pattern="unsafe",
        severity="MEDIUM",
        description="a well-behaved rule",
        regex=regex.compile("unsafe"),
    )
    text = "a" * 40 + " this content is unsafe"

    matches = scan(text, [_CATASTROPHIC_RULE, normal_rule])

    assert any(m.rule == "normal_rule" for m in matches), "a catastrophic rule must not block other rules from running"
