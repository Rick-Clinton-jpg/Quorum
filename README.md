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

## Failure handling, tested not just described

- **Firestore unavailable** (audit trail or IntentGraph session state):
  falls back to local disk automatically, including mid-write on a live
  Firestore failure — `gate/tests/test_firestore_audit.py::test_falls_back_to_disk_on_live_firestore_write_failure`
  and the equivalent in `test_firestore_intent.py` simulate the outage,
  not just describe the intended behavior.
- **The Gemini/Vertex AI call itself fails** (timeout, rate limit,
  transient outage): raises a typed `WorkerAgentCallError`
  (`worker_agent/orchestrator.py`), mapped by `service/main.py` to a
  502, not a generic 500 — a caller can tell "the upstream model call
  failed, retry" apart from "something here is actually broken." Tested
  by mocking the ADK call to fail (`worker_agent/tests/test_orchestrator.py`).
- **The Worker Agent's own self-check runs sandboxed**: patches are
  applied and tested in a throwaway temp copy of the target repo
  (`worker_agent/self_check.py`) — the real vendored `verifiers/sentry/`
  tree is never touched by anything the agent drafts.
- **The local Firestore fallback had a real lost-update race**: two
  requests for the same `session_id`, only reachable when Firestore is
  down, could race on `FirestoreIntentStore`'s load→mutate→save
  sequence and silently drop one update. `session_lock()`
  (`gate/firestore_intent.py`) closes it with a per-session lock held
  across the whole sequence, not just around the save — tested by
  reproducing the race deterministically with a `threading.Barrier`,
  then confirming the lock actually prevents it
  (`gate/tests/test_firestore_intent.py`).
- **A session's IntentGraph can outgrow Firestore's 1 MiB document
  limit**: production always runs the offline hashing-vectorizer
  embedding backend (see below), so a single session can approach that
  limit in well under a hundred turns. `_prune_for_firestore()`
  (`gate/firestore_intent.py`) drops the oldest non-safety-boundary
  nodes before every write rather than letting the write fail and
  silently fall back forever; a session that fell back locally during
  a real outage is merged back honestly, not discarded, the next time
  Firestore is reachable (`gate/tests/test_firestore_intent.py`).
- **A Worker Agent-drafted regex rule could hang the scanner**:
  `rules/default_rules.json` is a file the Worker Agent proposes diffs
  to, so a rule's pattern is untrusted input — a catastrophic-
  backtracking pattern could otherwise hang Sentry's `scan()` (and the
  whole gate with it) indefinitely. `verifiers/sentry/src/sentry/engine.py`
  now enforces a real per-rule matching timeout, tested against an
  actual runaway pattern, not a mocked one
  (`verifiers/sentry/tests/test_redos_defense.py`).
- **`retry_gate()`'s redraft loop had no bound on total wall time**:
  worst case, each attempt could cost minutes and the loop allowed
  several — unbounded, one HTTP request could legitimately run for
  tens of minutes. `RETRY_GATE_OUTER_DEADLINE_SECONDS`
  (`gate/quorum_gate.py`) now stops a new attempt from starting once
  spent, preserving the last attempt's real verdict
  (`gate/tests/test_retry_gate.py`).

Built for the All Things Agentic Hackathon, The Taskmaster track.

## Benchmarks

Measured locally (`gate/tests/fixtures/markdown_exfil_proposal.json`,
20 runs after a warm-up call, median reported) — the deterministic
verifier path only, no live Gemini/Firestore calls:

| Stage | Median latency |
|---|---|
| Full `run_gate()` (Sentry + IntentGraph + Kernel, all three stages) | ~4ms |
| Sentry `scan()` alone (all six default rules) | ~0.24ms |

Sentry's real per-rule matching budget (`RULE_TIMEOUT_SECONDS = 1.0s`,
see "Failure handling" above) is ~4,000x this measured cost on
ordinary input — the ReDoS defense only ever engages against an
actually pathological pattern, not normal traffic. The Gemini/Vertex AI
call that precedes the gate (drafting the proposal) dominates
end-to-end latency by a wide margin and isn't included above; that
call is bounded separately (`CALL_TIMEOUT_SECONDS = 180s`,
`worker_agent/orchestrator.py`).

Full local test suite (`gate/`, `verifiers/sentry/`, `service/`,
`worker_agent/` — 143 tests, no network calls): ~10s wall time on the
same machine.

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

# Full gate test suite (90 tests, no network calls)
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
- **Firestore's cross-instance lock is still open.** `gate/firestore_intent.py`'s
  `session_lock()` is a `threading.Lock()` — it fully closes the
  load-mutate-save race between two requests on the same Cloud Run
  *instance*, but not between two different *instances* (the real
  Firestore write is a plain `.set()`, not a compare-and-swap). Under
  that race, `merge_intent_graphs()` no longer silently discards a
  genuinely divergent node on the next reconciling load — it keeps
  both under a fresh id — but the underlying race itself needs a
  Firestore transaction wrapping the whole load-mutate-save cycle to
  actually close, which is not yet implemented.
- **Sentry is pattern-based defense in depth, not comprehensive
  injection/PII protection.** Independently re-audited: a plain,
  unobfuscated "ignore previous instructions" is now caught
  (`injection_trigger_phrase`, added after that audit), but zero-width
  character splitting, Unicode homoglyph substitution, phone numbers,
  API-key-shaped secrets, and Markdown-image exfiltration
  (`gate/tests/fixtures/markdown_exfil_proposal.json` is this project's
  own worked example of that exact gap) all still pass Sentry's rules
  with zero findings. Markdown-exfil detection specifically couldn't be
  added directly to the live ruleset without breaking that same fixture
  self-referentially (a rule's own positive test-case example text, in
  the diff that adds it, trips the rule it's demonstrating) — closing
  it properly needs Sentry to treat a diff's own test/example content
  differently from its detection logic, not just one more regex.
- Claim verification (`determine_claim_origin`) is lexical, not
  semantic — it can confirm a claim quotes its source accurately, not
  that the claim's underlying assertion is true. VERIFIED is granted
  only from an exact quoted span found verbatim at a specific line;
  keyword/topic overlap alone (even 100% of a claim's distinctive
  words, with no negation and no quotes) can only ever produce
  REPORTED now — independently re-audited to close a positively-phrased
  false claim ("The gate rejects supported Kernel claims" — actually
  false) that survived an earlier, narrower negation-only fix. A claim
  that never quotes anything verbatim from its cited source is
  REPORTED regardless of how true it actually is — confirming an
  *unquoted* factual assertion is the Reasoning Kernel's job
  (semantic), not this fallback's (lexical).

See `docs/INTEGRATION_MAP.md` for the full, disclosed limitations of
every vendored verifier.

## License
Licensed under PolyForm Noncommercial 1.0.0 — free to use, including
personal and research use. Commercial use requires a separate license.
See [LICENSE](./LICENSE) for full terms, or reach out to discuss
commercial licensing. This covers Quorum's own original code (the
Worker Agent orchestration and service layer).

Quorum vendors six components, each under its own PolyForm Noncommercial
1.0.0 terms: Trust Boundary (`gate/`), the orchestration layer that runs
the pipeline; three deterministic verifiers that judge every patch —
Sentry, Reasoning Kernel, and IntentGraph (`verifiers/intent_graph/`,
from the `intent-layer` repo); Warden, which records the audit trail;
and Review-Board, a human-gated review step rather than an automated
one. All six were built within this same contest's Submission Period
(Aug 3–31, 2026) — the oldest, reasoning-kernel, on Aug 6 — as separate
repos before being consolidated into Quorum on Aug 18; none of this
predates the hackathon. Two were substantively modified for Quorum, not
just vendored as-is: Sentry's ruleset gained three new detection rules
(`prior_approval_claim`, `pii_exposure_pattern`, `rule_disablement_request`)
written specifically for gaps this project's own adversarial testing
found, and its engine (`engine.py`) was switched from stdlib `re` to
the third-party `regex` module with a real per-rule matching timeout —
`rules/default_rules.json` is a file the Worker Agent proposes diffs
to, so a rule's pattern is untrusted input, and stdlib `re` has no way
to bound a catastrophic-backtracking match's wall-clock time. IntentGraph's
`extractor.py` had its embedding-backend logic rewritten after
`sentence-transformers` failed to load in the deployed environment.
`gate/`, Warden, and Review-Board are used unmodified — all
coordination happens from `gate/quorum_gate.py` calling into them. See
`docs/INTEGRATION_MAP.md` for what each one actually does and doesn't do.
