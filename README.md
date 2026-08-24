# Quorum

A gated coordinator for an autonomous coding agent. A **Worker Agent**
(Gemini 3.5, via Google's Agent Development Kit, running on Vertex AI)
drafts small code patches; nothing it drafts ships until three
independent, deterministic verifiers agree.

```
task description
      |
      v
Worker Agent (ADK + Gemini 3.5 via Vertex AI) ---> Proposal (JSON)
      |
      v
Gate (gate/quorum_gate.py)
  Sentry        -- scans the proposal's OWN diff/rationale for injected content
  IntentGraph   -- checks for a reformulated return to a rejected objective
  Reasoning Kernel -- independently re-verifies every claim's provenance
  (every stage logged to Warden's audit trail, Firestore-backed)
      |
      v
PASS / REJECT / ESCALATE
      |
  PASS -> opens a real pull request (gate/github_action.py)
  REJECT -> feeds the reason back to the Worker Agent for one auto-redraft
  ESCALATE -> pauses for a human (Review-Board has no programmatic API, by design)
```

Live on Cloud Run: `https://quorum-coordinator-497954606552.us-central1.run.app`

Built for the All Things Agentic Hackathon, Fortified Enterprise Fleet track.

## Repository layout

- `worker_agent/` — the ADK agent that drafts and self-checks proposals. See `worker_agent/README.md`.
- `gate/` — the coordinator (`quorum_gate.py`), the action loop (`github_action.py`), and Firestore-backed persistence (`firestore_audit.py`, `firestore_intent.py`).
- `service/` — the FastAPI app deployed to Cloud Run. See `service/README.md` for deploy steps.
- `verifiers/` — vendored source for the six safety tools this project gates on (Sentry, IntentGraph, Reasoning Kernel, Review-Board, Warden). See `docs/INTEGRATION_MAP.md` for exact APIs and disclosed limitations.
- `docs/INTEGRATION_MAP.md` — the full technical map of every vendored component: real signatures, what's pip-installable, every disclosed limitation and design tradeoff.

## Reproducible testing

### Local (no GCP account needed)

```bash
cd worker_agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -r ../gate/requirements-quorum.txt
pip install -r ../service/requirements.txt   # for gate/github_action.py, pytest, etc.

# Full gate test suite (34 tests, no network calls)
python3 -m pytest ../gate/tests/ -v

# Worker Agent alone (needs GOOGLE_API_KEY or Vertex AI credentials - see worker_agent/README.md)
python3 -m worker_agent.cli "your task description here"
```

### Against the live deployed service

```bash
SERVICE_URL=https://quorum-coordinator-497954606552.us-central1.run.app

# Health check
curl "$SERVICE_URL/status"

# Evaluate an already-drafted proposal through the gate (no Gemini call)
curl -X POST "$SERVICE_URL/gate/run" \
  -H "Content-Type: application/json" \
  -d @gate/tests/fixtures/markdown_exfil_proposal.json  # wrap under {"proposal": ..., "session_id": "test"}

# Full live chain: Worker Agent drafts (real Gemini 3.5 call via Vertex AI),
# then the gate evaluates it. On PASS, opens a real pull request.
curl -X POST "$SERVICE_URL/gate/retry" \
  -H "Content-Type: application/json" \
  -d '{"task_description": "<a real gap in the target ruleset>", "session_id": "demo"}'

# The append-only audit trail (Sentry/IntentGraph/Kernel), Firestore-backed
curl "$SERVICE_URL/audit/trail"
```

Example pull requests opened autonomously by the deployed service on a
`PASS` verdict: [#1](https://github.com/Rick-Clinton-jpg/Quorum/pull/1),
[#2](https://github.com/Rick-Clinton-jpg/Quorum/pull/2),
[#3](https://github.com/Rick-Clinton-jpg/Quorum/pull/3).

## What's honestly not finished

- IntentGraph's embeddings are lexical (an offline hashing vectorizer),
  not the semantic `sentence-transformers` model originally planned —
  that model has never successfully loaded in any real environment this
  project has run in.
- The action opens pull requests against a working branch, not `main`
  — appropriate for a hackathon demo, the obvious next step before
  broader use.
- Cloud Run's cold-start cost hasn't been fully root-caused — one real
  contributor was found and fixed; that doesn't explain all of it.

See `docs/INTEGRATION_MAP.md` for the full, disclosed limitations of
every vendored verifier.

## License
Licensed under PolyForm Noncommercial 1.0.0 — free to use, including
personal and research use. Commercial use requires a separate license.
See [LICENSE](./LICENSE) for full terms, or reach out to discuss
commercial licensing. This covers Quorum's own original code (the
Worker Agent orchestration and service layer).

Quorum vendors six independently-developed components, each under its
own PolyForm Noncommercial 1.0.0 terms: Trust Boundary (`gate/`), the
orchestration layer that runs the pipeline; three deterministic
verifiers that judge every patch — Sentry, Reasoning Kernel, and
IntentGraph (`verifiers/intent_graph/`, from the `intent-layer` repo);
Warden, which records the audit trail; and Review-Board, a human-gated
review step rather than an automated one. See `docs/INTEGRATION_MAP.md`
for what each one actually does and doesn't do.
