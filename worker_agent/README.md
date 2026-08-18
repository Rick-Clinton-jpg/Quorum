# Worker Agent (Phase 2)

An ADK agent that reads Sentry's real source, drafts one detection-rule
patch for a given task description, self-checks it against Sentry's own
pytest suite (revising once if it fails), and prints a single structured
JSON `Proposal`.

**It does not call any verifier and does not open a PR.** Those only
happen after a PASS verdict from the gate, which is Phase 3 and doesn't
exist yet.

## Setup

```bash
cd worker_agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in credentials, see below - .env is gitignored
```

Two ways to authenticate (either satisfies the hackathon's "Gemini 3.5+
via Gemini API or Vertex AI" requirement):

- **Gemini Developer API** (fastest to get running): set `GOOGLE_API_KEY`,
  `GOOGLE_GENAI_USE_VERTEXAI=FALSE`.
- **Real Vertex AI** (what the deployed Cloud Run service should use):
  `GOOGLE_GENAI_USE_VERTEXAI=TRUE`, `GOOGLE_CLOUD_PROJECT`,
  `GOOGLE_CLOUD_LOCATION`, and Application Default Credentials available
  to the process. Nothing in this package's code changes between the two
  — `agent.py` just passes the model name string `"gemini-3.5-flash"` and
  the `google-genai` client resolves the backend from environment
  variables at call time.

## Run

```bash
python3 -m worker_agent.cli "<task description>"
python3 -m worker_agent.cli --task-file gap_analysis.md --out /tmp/proposal.json
```

Prints a `Proposal` JSON object to stdout (schema: `schema.py`). Exit code
is `0` if `self_check_result.passed`, `1` otherwise — so Phase 3 can shell
out to this directly and branch on exit code without parsing JSON first.

## How it works

1. **`agent.py`** — one ADK `LlmAgent` (Gemini 3.5 Flash), with two
   read-only tools (`tools.py`) scoped to `verifiers/sentry/`:
   `list_sentry_files()` and `read_sentry_file(relative_path)`. Its
   instruction requires it to actually read `rules/default_rules.json`
   and `tests/test_rules_detection.py` before drafting anything — this
   project's own history (`docs/INTEGRATION_MAP.md`,
   `gate/pipeline.py`'s two documented bugs) is why "confirm the
   convention by reading it" is a hard rule here, not a suggestion.
2. The agent's structured output (`DraftProposal`) is enforced via ADK's
   `output_schema` — Gemini calls a `set_model_response` tool with the
   final JSON once it's done researching, rather than free-texting it.
3. **`self_check.py`** deterministically (no LLM involved) applies the
   draft to a temp copy of `verifiers/sentry/` — inserting the new rule
   into `default_rules.json` and the new test case into
   `test_rules_detection.py`'s `CASES` dict, exactly matching their
   existing formats — and runs `pytest -v` there. A malformed draft
   (duplicate rule name, regex that doesn't compile, invalid severity)
   fails immediately as a `PatchError`, without spending a pytest run.
4. **`orchestrator.py`** runs steps 1–3, and on failure sends the pytest
   failure output back to the *same* agent session as one revision
   request, then re-checks. At most one revision (`MAX_ATTEMPTS = 2`),
   per the Phase 2 brief.
5. **`cli.py`** wraps all of the above as a standalone script.

## What's deliberately NOT here

- No call to `sentry.scan()` (Sentry as a *verifier* of the Worker
  Agent's own output, as opposed to Sentry as the *target repo* being
  patched — these are different roles, see `docs/INTEGRATION_MAP.md`).
- No call to Reasoning Kernel or IntentGraph.
- No PR creation, no `git commit`, no write to `verifiers/sentry/`
  itself — `self_check.py` only ever touches a `tempfile.TemporaryDirectory`
  copy.

`schema.py` documents, field by field, how the output is already shaped
for those future integrations without calling any of them yet.
