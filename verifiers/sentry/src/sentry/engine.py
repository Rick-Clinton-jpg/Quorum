"""Rule loading and scanning engine for Sentry."""

from __future__ import annotations

import json
# stdlib `re` has no way to bound a match's wall-clock time - a
# catastrophic-backtracking pattern (e.g. "(a|aa)+b") runs to
# completion or not at all, and rules/default_rules.json is exactly
# the file Quorum's Worker Agent proposes diffs to (see
# verifiers/sentry/rules/default_rules.json's own git history and
# gate/tests/fixtures/escalate_demo_proposal.json), so an
# agent-drafted pattern reaching this file is untrusted input, not a
# maintainer-reviewed one. The third-party `regex` module is a
# near-drop-in replacement that accepts a real `timeout=` on
# match/search/finditer, enforced during matching (confirmed against
# an actual "(a|aa)+b" x 30 char runaway locally: raises TimeoutError
# at the budget instead of hanging) - a per-pattern denylist of "risky
# shapes" would only be a real ReDoS defense until the next shape.
import regex
from dataclasses import dataclass
from pathlib import Path

VALID_SEVERITIES = {"HIGH", "MEDIUM", "LOW"}

# Generous for any legitimate rule against realistic proposal-sized text
# (all current default rules finish in well under a millisecond) while
# still bounding the worst case to a small, fixed cost per rule per scan.
RULE_TIMEOUT_SECONDS = 1.0

# rules/default_rules.json lives at the repo root, two levels above this file
# (src/sentry/engine.py -> src/sentry -> src -> repo root).
DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "rules" / "default_rules.json"


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: str
    severity: str
    description: str
    regex: regex.Pattern


@dataclass(frozen=True)
class Match:
    rule: str
    severity: str
    description: str
    start: int
    end: int
    text: str


class RuleLoadError(ValueError):
    """Raised when a rules file is malformed or contains an invalid pattern."""


def load_rules(path: str | Path = DEFAULT_RULES_PATH) -> list[Rule]:
    """Load a rules JSON file and compile each pattern as a regex.

    Fails loudly (raises RuleLoadError) if the file is malformed, a rule is
    missing required fields, has an invalid severity, or its pattern does not
    compile as a valid regex.
    """
    path = Path(path)
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise RuleLoadError(f"Rules file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuleLoadError(f"Rules file is not valid JSON: {path} ({exc})") from exc

    if not isinstance(raw, list):
        raise RuleLoadError(f"Rules file must contain a JSON array of rule objects: {path}")

    rules: list[Rule] = []
    seen_names: set[str] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise RuleLoadError(f"Rule at index {i} is not an object")

        missing = {"name", "pattern", "severity", "description"} - entry.keys()
        if missing:
            raise RuleLoadError(f"Rule at index {i} is missing required field(s): {sorted(missing)}")

        name = entry["name"]
        pattern = entry["pattern"]
        severity = entry["severity"]
        description = entry["description"]

        if name in seen_names:
            raise RuleLoadError(f"Duplicate rule name: {name!r}")
        seen_names.add(name)

        if severity not in VALID_SEVERITIES:
            raise RuleLoadError(
                f"Rule {name!r} has invalid severity {severity!r}; must be one of {sorted(VALID_SEVERITIES)}"
            )

        try:
            compiled = regex.compile(pattern)
        except regex.error as exc:
            raise RuleLoadError(f"Rule {name!r} has an invalid regex pattern {pattern!r}: {exc}") from exc

        rules.append(
            Rule(
                name=name,
                pattern=pattern,
                severity=severity,
                description=description,
                regex=compiled,
            )
        )

    return rules


def scan(text: str, rules: list[Rule] | None = None) -> list[Match]:
    """Scan text against the given rules (or the default ruleset) and return all matches."""
    if rules is None:
        rules = load_rules()

    matches: list[Match] = []
    for rule in rules:
        try:
            rule_matches = list(rule.regex.finditer(text, timeout=RULE_TIMEOUT_SECONDS))
        except TimeoutError:
            # Fail closed, not open: a pattern that can't be safely
            # evaluated within budget is itself the finding - forced to
            # HIGH regardless of the rule's own declared severity, since
            # "this rule is a ReDoS risk against real input" outranks
            # whatever the rule was originally meant to detect. Text is
            # not echoed back here (unlike a normal Match) since the
            # scanned content that triggered the runaway match may be
            # large or itself sensitive.
            matches.append(
                Match(
                    rule=rule.name,
                    severity="HIGH",
                    description=f"rule {rule.name!r} exceeded its {RULE_TIMEOUT_SECONDS}s matching budget "
                    "against this input - unsafe to evaluate, treated as a finding rather than skipped",
                    start=0,
                    end=len(text),
                    text="",
                )
            )
            continue
        for m in rule_matches:
            matches.append(
                Match(
                    rule=rule.name,
                    severity=rule.severity,
                    description=rule.description,
                    start=m.start(),
                    end=m.end(),
                    text=m.group(0),
                )
            )
    return matches
