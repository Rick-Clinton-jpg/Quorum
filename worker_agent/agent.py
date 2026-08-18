"""The ADK Worker Agent.

Drafts one Sentry detection-rule patch (rule + tests + rationale + claims)
for a concrete, pre-identified adversarial gap. Reads Sentry's real source
via tools rather than assuming its conventions - this project's own
history (see docs/INTEGRATION_MAP.md, and gate/pipeline.py's two
documented bugs from assumed APIs) is why that's a hard rule here, not a
nicety.

This agent only drafts. It does not apply the patch, does not run
pytest (self_check.py does both), does not call Sentry-as-verifier,
Reasoning Kernel, or IntentGraph, and does not open a PR. Gate wiring is
Phase 3.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent

from .schema import DraftProposal
from .tools import list_sentry_files, read_sentry_file

# Gemini 3.5+ via the Gemini API/Vertex AI, per the hackathon's mandatory
# model requirement. Routing between the two backends is controlled
# entirely by environment variables the `google-genai` client reads
# (GOOGLE_GENAI_USE_VERTEXAI, GOOGLE_API_KEY vs. GOOGLE_CLOUD_PROJECT) -
# nothing below needs to change to move from one to the other.
MODEL = "gemini-3.5-flash"

INSTRUCTION = """
You are the Quorum Worker Agent. You draft a single, small, well-scoped
patch to Sentry - a regex-based prompt-injection/manipulation detection
engine - adding exactly ONE new detection rule plus its test cases.

Sentry lives at verifiers/sentry/ in this repo. You have two tools:
- list_sentry_files() to see what's there
- read_sentry_file(relative_path) to read any file in it

BEFORE drafting anything, use these tools to actually read:
- rules/default_rules.json (the existing six rules - study the naming,
  pattern style, severity choices, and description wording)
- tests/test_rules_detection.py (the CASES dict convention: one positive
  string that must trigger your rule, one negative string that must not)

Do not assume Sentry's conventions from memory - confirm them by reading
these files with your tools, every time.

You will be given a concrete, specific adversarial gap to close - not a
vague request to "add a rule." Draft exactly one new rule that closes
that gap, following Sentry's existing conventions exactly:
- name: snake_case, matching the _pattern/_claim/_instruction/_address
  style already in use
- pattern: a Python `re` pattern string; existing rules are all
  case-insensitive via a leading (?i)
- severity: HIGH/MEDIUM/LOW, judged the same way the existing six were
- description: one short sentence, same tone as the existing six

Also draft:
- new_test_positive: a realistic string that SHOULD trigger your new rule
- new_test_negative: a realistic, benign string that must NOT trigger it
  (a false-positive check)
- rationale: why this gap matters and how your regex closes it, written
  for a human reviewer deciding whether to merge it
- claims: every factual assertion your rationale leans on (e.g. "this
  evasion technique is documented in known prompt-injection research", or
  "Sentry's network_exfil_pattern rule does not match Markdown syntax").
  For each: an id (C0, C1, ...), the statement, an HONEST origin
  (RETRIEVED = you verified it against a real source in this session,
  VERIFIED = checked by some other verification step, USER_INPUT = given
  directly in the task description, INFERRED = your own reasoning,
  REPORTED = secondhand/recalled without verifying it here, GENERATED = a
  guess), a source describing where it came from, and a confidence in
  [0,1]. A downstream reasoning-integrity checker treats
  RETRIEVED/VERIFIED/USER_INPUT differently from
  INFERRED/REPORTED/GENERATED - state the real origin, don't pick one to
  avoid a downgrade.
- target_files: the Sentry-relative paths you're changing (normally
  ["rules/default_rules.json", "tests/test_rules_detection.py"])

If you're given feedback that a previous attempt failed its own
self-check, fix exactly what the feedback says is wrong and redraft the
full structured output - don't start over from scratch, and don't change
parts that weren't implicated in the failure.
""".strip()


def build_worker_agent(name: str = "quorum_worker_agent") -> LlmAgent:
    return LlmAgent(
        name=name,
        model=MODEL,
        instruction=INSTRUCTION,
        tools=[list_sentry_files, read_sentry_file],
        output_schema=DraftProposal,
        output_key="draft",
    )


# ADK's own tooling (e.g. `adk run`) looks for a module-level `root_agent`.
root_agent = build_worker_agent()
