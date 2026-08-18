"""Every pattern in the default ruleset must compile as a valid regex."""

import json
import re
from pathlib import Path

from sentry.engine import DEFAULT_RULES_PATH, load_rules

RAW_RULES = json.loads(Path(DEFAULT_RULES_PATH).read_text())


def test_all_raw_patterns_compile():
    for entry in RAW_RULES:
        re.compile(entry["pattern"])


def test_load_rules_succeeds_and_returns_compiled_regexes():
    rules = load_rules()
    assert len(rules) == len(RAW_RULES)
    for rule in rules:
        assert isinstance(rule.regex, re.Pattern)


def test_rule_names_are_unique():
    names = [entry["name"] for entry in RAW_RULES]
    assert len(names) == len(set(names))


def test_rule_severities_are_valid():
    for entry in RAW_RULES:
        assert entry["severity"] in {"HIGH", "MEDIUM", "LOW"}
