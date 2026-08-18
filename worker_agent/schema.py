"""Structured types shared by the Worker Agent, its self-check, and its CLI.

Shaped against docs/INTEGRATION_MAP.md's confirmed read of what Phase 3's
verifiers actually expect, without wiring any calls to them yet:

- `claims[]` mirrors the FACT-node fields `gate/pipeline.py`'s
  `record_review_board_outcome()` already builds from
  (id/label/origin/source/confidence) so Phase 3 can map a claim onto a
  Reasoning Kernel FACT node close to 1:1. `origin` uses the Kernel's own
  `Origin` enum values (kernel.py) rather than inventing a new vocabulary.
- `diff`/`rationale` are the free text a Sentry-as-verifier scan
  (`sentry.scan(text) -> list[Match]`, confirmed in
  verifiers/sentry/src/sentry/engine.py) would run against in Phase 3 —
  kept as plain strings, not embedded in a nested structure, so they're
  directly scannable.
- `task_description` is carried through to the top level because it's the
  natural per-turn text an IntentGraph integration
  (`IntentGraph.add_turn(text, timestamp)`, confirmed in
  verifiers/intent_graph/intent_layer/graph.py) would feed in across
  retries, to detect a rejected objective re-entering under a new
  description.

None of Sentry, the Kernel, or IntentGraph are imported or called here —
that's Phase 3.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# Matches kernel.py's Origin enum values exactly (verifiers/reasoning_kernel/kernel.py).
ClaimOrigin = Literal[
    "RETRIEVED", "USER_INPUT", "VERIFIED", "INFERRED", "REPORTED", "GENERATED"
]
Severity = Literal["HIGH", "MEDIUM", "LOW"]


class NewRule(BaseModel):
    """One new entry for verifiers/sentry/rules/default_rules.json."""

    name: str = Field(description="snake_case rule name, matching Sentry's existing _pattern/_claim/_instruction/_address naming style")
    pattern: str = Field(description="A Python `re` pattern string; Sentry's existing rules are all case-insensitive via a leading (?i)")
    severity: Severity
    description: str = Field(description="One short sentence, same tone as Sentry's existing six rule descriptions")


class Claim(BaseModel):
    """One factual assertion the rationale depends on.

    Field names/values deliberately track kernel.py's FACT-node shape
    (id/label~statement/origin/source/confidence) so Phase 3 can build a
    Kernel claim graph from this list with minimal translation - not yet
    wired, but shaped for it.
    """

    id: str = Field(description='Short id, e.g. "C0", "C1"')
    statement: str
    origin: ClaimOrigin = Field(
        description=(
            "Honest account of where this claim comes from. RETRIEVED/VERIFIED/"
            "USER_INPUT are the origins Reasoning Kernel's Rule 1 treats as "
            "verifiable for a FACT node; INFERRED/REPORTED/GENERATED get "
            "downgraded to an ASSUMPTION by that same rule. State the real "
            "origin - don't pick one to avoid the downgrade."
        )
    )
    source: str = Field(description="Where the claim came from, in enough detail to check it")
    confidence: float = Field(ge=0.0, le=1.0)


class DraftProposal(BaseModel):
    """The Worker Agent's structured output for one drafting attempt."""

    target_files: list[str] = Field(
        description="Sentry-repo-relative paths being changed, e.g. rules/default_rules.json"
    )
    new_rule: NewRule
    new_test_positive: str = Field(description="A realistic string that SHOULD trigger new_rule")
    new_test_negative: str = Field(description="A realistic, benign string that must NOT trigger new_rule")
    rationale: str = Field(description="Why this rule is needed and how it works, written for a human reviewer")
    claims: list[Claim] = Field(description="Every factual claim the rationale leans on")


class SelfCheckResult(BaseModel):
    """Outcome of applying a DraftProposal to a throwaway Sentry copy and
    running its real pytest suite. This is the agent's own sanity check,
    not the deterministic gate verdict Phase 3 will add."""

    passed: bool
    attempts: int
    existing_suite_passed: bool
    new_test_passed: bool
    pytest_summary: str
    revision_notes: Optional[str] = None


class Proposal(BaseModel):
    """The single JSON object worker_agent's CLI prints - Phase 3's input."""

    task_description: str
    model: str
    generated_at: str
    target_files: list[str]
    diff: str
    rationale: str
    claims: list[Claim]
    self_check_result: SelfCheckResult
