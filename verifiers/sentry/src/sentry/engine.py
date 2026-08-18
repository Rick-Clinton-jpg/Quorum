"""Rule loading and scanning engine for Sentry."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

VALID_SEVERITIES = {"HIGH", "MEDIUM", "LOW"}

# rules/default_rules.json lives at the repo root, two levels above this file
# (src/sentry/engine.py -> src/sentry -> src -> repo root).
DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "rules" / "default_rules.json"


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: str
    severity: str
    description: str
    regex: re.Pattern


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
            regex = re.compile(pattern)
        except re.error as exc:
            raise RuleLoadError(f"Rule {name!r} has an invalid regex pattern {pattern!r}: {exc}") from exc

        rules.append(
            Rule(
                name=name,
                pattern=pattern,
                severity=severity,
                description=description,
                regex=regex,
            )
        )

    return rules


def scan(text: str, rules: list[Rule] | None = None) -> list[Match]:
    """Scan text against the given rules (or the default ruleset) and return all matches."""
    if rules is None:
        rules = load_rules()

    matches: list[Match] = []
    for rule in rules:
        for m in rule.regex.finditer(text):
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
