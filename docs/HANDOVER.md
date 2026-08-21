# Quorum — Handover

Written for whoever (human or agent) picks this project up next to finish
it before the Aug 31, 2026 5pm PDT deadline. Everything below is verified
against the actual repo state at commit `4edb47f`, not recalled from
memory — check `git log` if this doc and the code ever disagree, the code
wins.

## 1. What this project is

Quorum governs an autonomous Gemini coding agent (the **Worker Agent**)
that is not trusted to act on its own say-so. It drafts a small, scoped
code patch, and a deterministic **gate** routes that patch through four
of six vendored AI-safety tools before anything could ship:

```
task description
      |
      v
Worker Agent (ADK + Gemini 3.5) ---> structured Proposal (JSON)
      |
      v
Gate coordinator (gate/quorum_gate.py)
  Stage A: Sentry      -- scans the proposal's OWN diff/rationale for
                           injected/manipulative content
  Stage B: IntentGraph -- checks the task description isn't a
                           reformulated return to a rejected objective
  Stage C: Reasoning Kernel -- checks the claims backing the rationale
                           have real, gate-verified provenance
  (every stage logged to Warden's append-only audit trail)
      |
      v
PASS / REJECT / ESCALATE
```

- `PASS` — clean.
- `REJECT` — Sentry HIGH finding, or Kernel says REFUTED/ERROR. The gate
  retries once, automatically, feeding the rejection reason back to the
  Worker Agent for a redraft.
- `ESCALATE` — IntentGraph flags HIGH re-entry risk, or the Kernel
  couldn't clearly resolve the claim graph (NEI/INCONCLUSIVE/ABSTAIN).
  **Never retried automatically** — this is exactly what Review-Board
  exists for, and Review-Board has zero programmatic API (see §3). It's
  a pause for a human, not a code path.

Nothing here **applies** a patch or opens a PR yet. That was intentionally
out of scope for every phase built so far — see §6 for what's left.

## 2. Hackathon requirement status

| Requirement | Status | Where |
|---|---|---|
| Gemini 3.5+ via Gemini API or Vertex AI | ✅ Done | `worker_agent/agent.py` — `model = "gemini-3.5-flash"`. Currently authenticates via the Gemini Developer API (`GOOGLE_API_KEY`); switching to real Vertex AI is an env-var change only, no code change — see §5. |
| Google Agent Framework (ADK) | ✅ Done | `worker_agent/agent.py` — one `google.adk.agents.LlmAgent` with tool-calling + structured `output_schema`. |
| GCP infra service (Cloud Run / Firestore) | ❌ Not started | This is the entire remaining scope — see §6. |

## 3. What's built (Phases 1–3, all committed and tested)

### Phase 1 — Integration audit (commit `bddf97d`)

All six safety repos vendored as real source under `verifiers/` (plus
`gate/` for Trust Boundary) — not submodules, not assumed APIs. Every
public entry point was read from actual source before any integration
code was written. Full findings: **`docs/INTEGRATION_MAP.md`** — read this
before touching any verifier's code, it documents exact signatures, what's
pip-installable vs. not, and every disclosed limitation.

Headline findings that constrain everything downstream:

- **Review-Board has zero programmatic API.** It's a Claude Code skill
  (`verifiers/review_board/SKILL.md`, `disable-model-invocation: true`),
  invoked only by a human typing `/review-board`. It cannot be called from
  code, by its own design (adversarial testing showed auto-triggering
  misfires). Do not build anything that assumes otherwise.
- **Warden's drift detector is confirmed inverted** (AUC 0.332, disclosed
  in its own README). Only `warden.audit.AuditLogger` (append-only log) is
  used anywhere in this project. Never `warden.matcher` / `dual_compare`.
- **Reasoning Kernel and IntentGraph have no packaging** (no
  `pyproject.toml`/`setup.py`) — they only work via direct `sys.path`
  insertion, which `gate/quorum_paths.py` handles (see §3.3).
- Kernel's real API is a graph/transaction engine
  (`grant`/`begin`/`add_node`/`add_edge`/`commit()`), not the
  `Kernel.evaluate()` an earlier assumption expected.

### Phase 2 — Worker Agent (commit `548b4f6`)

**Location:** `worker_agent/`. **Entry point:**
`python -m worker_agent.cli "<task description>"` (see §5 for setup).

The agent (`worker_agent/agent.py`, `build_worker_agent()` /
module-level `root_agent`) is a single ADK `LlmAgent` with:
- Two read-only tools (`worker_agent/tools.py`): `list_sentry_files()`,
  `read_sentry_file(relative_path)`, scoped to `verifiers/sentry/` only.
- `output_schema=DraftProposal` (Pydantic, `worker_agent/schema.py`) —
  ADK enforces structured output even with tools active (this ADK version,
  2.7.1, supports both together via an internal `set_model_response` tool).

Flow (`worker_agent/orchestrator.py`, `run_worker_agent(task_description)`):
1. Agent reads Sentry's real rules/tests via its tools, drafts a new rule
   + test cases + rationale + claims.
2. `worker_agent/self_check.py` deterministically (no LLM) applies the
   draft to a **temp copy** of `verifiers/sentry/` (never the vendored
   tree itself) and runs Sentry's real `pytest` suite there.
3. On failure, one revision round-trip: the pytest failure is sent back to
   the *same agent session* for a redraft, then re-checked once more.
   `MAX_ATTEMPTS = 2` in `orchestrator.py`.
4. Output: one `Proposal` (Pydantic, `schema.py`) — `task_description`,
   `model`, `generated_at`, `target_files`, `diff` (real unified diff,
   deterministically generated by `self_check.py` from the applied
   changes — the LLM drafts rule/test *fields*, not diff text directly),
   `rationale`, `claims[]`, `self_check_result`.

**Claim shape** (`schema.py::Claim`) — `id`, `statement`, `origin` (one of
Kernel's own `Origin` enum values: `RETRIEVED`/`USER_INPUT`/`VERIFIED`/
`INFERRED`/`REPORTED`/`GENERATED`), `source`, `confidence` (0–1). This is
the agent's own **self-report** — the gate does not trust it verbatim
(§3.3).

**Verified target for this build:** a `markdown_exfil_pattern` Sentry rule
closing a real gap (none of Sentry's 6 default rules examine Markdown
image/link syntax, so an auto-rendered `![](https://...&nbsp;?token=…)`
can exfiltrate data with zero matching code pattern). Full gap writeup:
`worker_agent/gap_analysis.md`. Real sample output used as the Phase 3
test fixture: `gate/tests/fixtures/markdown_exfil_proposal.json`.

### Phase 3 — Gate wiring (commit `4edb47f`)

**Location:** `gate/quorum_gate.py` (new — does not modify the vendored
`gate/pipeline.py`, see the governance note in §4). **Entry points:**
- `run_gate(proposal: dict, intent_graph: IntentGraph, audit=None) -> GateResult`
  — one proposal through all three verifier stages, once.
- `retry_gate(task_description: str, max_gate_attempts=2) -> (proposal, GateResult, history)`
  — runs the Worker Agent, then `run_gate`, and on `REJECT` feeds the
  gate's reasons back to the agent for one redraft. This is a **separate,
  outer** retry loop from the Worker Agent's own internal self-check loop
  (§3.2 step 3) — two different retry mechanisms at two different layers,
  by design.

**Kernel claim-graph translation** (`quorum_gate.py::build_claim_graph`) —
the part with real design content:
- One Kernel `FACT` node per `Claim`, one `CONCLUSION` node
  ("is the rationale supported"), `SUPPORTS` edges from every fact into
  it. Fed to `pipeline.py`'s existing, **unmodified**
  `record_review_board_outcome()` — reused exactly as Trust Boundary built
  it, not reimplemented.
- **`determine_claim_origin(claim)` never returns `claim["origin"]`
  verbatim.** It independently resolves `claim["source"]` to a real file
  under `verifiers/` or `gate/`, re-reads it, and checks the claim's own
  distinctive content (quoted spans, or ≥60% word overlap) actually
  appears there. `VERIFIED` only if that holds; everything else —
  external citations, vague sources, sources that don't check out —
  becomes `REPORTED`, which Kernel Rule 1 then REPAIR-demotes to
  `ASSUMPTION` (it's not in `FACT_ORIGINS`). This was a specific,
  explicit design requirement (see conversation history / Phase 3 brief)
  and is the one part of this gate you should be most careful not to
  weaken.
- Confidence `derivation` is always `"REASONING"` (not `"ASSERTED"`,
  which Kernel Rule 3 REJECTs outright) — documented rationale in the
  code comment at `build_claim_graph`.

**Verdict mapping** (`quorum_gate.py::run_gate`, in this priority order):
1. Sentry `REJECT` → gate `REJECT`, short-circuits before Kernel/IntentGraph
   even matter for the verdict (though IntentGraph still records the turn).
2. IntentGraph `HIGH` risk → gate `ESCALATE` (overrides Kernel).
3. Kernel `NEI`/`INCONCLUSIVE`/`ABSTAIN` → gate `ESCALATE`.
4. Kernel `REFUTED`/`ERROR` → gate `REJECT`.
5. Otherwise (Kernel `SUPPORTED`) → gate `PASS`.

A `REJECT` marks a `[SAFETY BOUNDARY TRIGGERED]` turn in the IntentGraph
session afterward (so a later reformulated resubmission is visible to
re-entry detection); `ESCALATE` does not (it isn't a confirmed-bad
outcome).

**Real bugs found and fixed while building this** (all covered by tests,
`gate/tests/test_quorum_gate.py`):

1. **Diff-scanning false positive.** Scanning a proposal's *entire* diff
   text with Sentry false-positived on exactly this project's own target
   repo: a patch to Sentry's own `rules/`/`tests/` file necessarily
   contains unchanged context lines that already look like exfiltration
   patterns (the existing `network_exfil_pattern` rule's own regex text).
   Fixed: `_added_lines()` scans only lines a unified diff actually adds.
2. **Import-order module-caching bug.** `pipeline.py` resolves its
   optional `from kernel import ...` once, at first import. If the
   vendored `gate/tests/test_pipeline.py` (which only needs `sentry` on
   `sys.path`) got collected by pytest before `test_quorum_gate.py` (which
   needs `kernel`/`intent_layer`/`warden` too, via `quorum_paths.py`),
   `Kernel` would cache as `None` for the rest of the session regardless
   of what ran later. Fixed with `gate/conftest.py`, which pytest
   guarantees loads before any test module in the directory.
3. **Disclosed, not "fixed":** Kernel's Rule 3 confidence-ceiling
   calculation runs *before* Rule 1's REPAIR pass, in the same `commit()`.
   A claim demoted `FACT`→`ASSUMPTION` (i.e. a `REPORTED`-origin claim)
   still contributes its full original confidence to that ceiling
   calculation on this same transaction — meaning a `CONCLUSION` can still
   read as `SUPPORTED` even when every backing claim just got downgraded.
   This is real, vendored Kernel behavior (`verifiers/reasoning_kernel/kernel.py`,
   `_cap()`), not something `quorum_gate.py` should paper over — see
   `gate/CONTRIBUTING.md`'s explicit boundary on touching verdict logic.
   **If you want this to behave differently, it requires either a
   Reasoning Kernel change (ask the repo owner first, per CONTRIBUTING.md)
   or a documented design decision in the gate to re-derive the
   CONCLUSION's confidence itself post-hoc — not yet done.**

**Test status:** `gate/tests/` — 13/13 passing (2 vendored
`test_pipeline.py` + 11 new `test_quorum_gate.py`, including an
end-to-end run against the real Phase 2 sample proposal).

## 4. Governance boundary — read before editing `gate/pipeline.py`

`gate/pipeline.py` is vendored Trust Boundary source, not owned by this
phase's work. `gate/CONTRIBUTING.md` explicitly reserves
`record_review_board_outcome()`, `_highest_severity()`, `DECISION_THRESHOLD`,
and any other verdict/threshold logic in that file for the repo owner to
change — an automated session may fix mechanical issues (lint, imports,
types) there without asking, but not verdict logic, even if a test is
failing because of it. `gate/quorum_gate.py` was built as new, separate
glue specifically to respect this boundary — extend that file, don't fork
`pipeline.py`.

## 5. Environment setup

```bash
cd worker_agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install numpy scikit-learn click pytest   # for gate/ tests (intent_layer + warden + self-check)
cp .env.example .env   # fill in credentials - .env is gitignored, never commit it
```

`.env` (loaded automatically by `worker_agent/__init__.py` via
`python-dotenv`, regardless of which module imports the package first):

```
GOOGLE_API_KEY=<a Gemini Developer API key>
GOOGLE_GENAI_USE_VERTEXAI=FALSE
```

**Current key is a free-tier key with no billing attached** (rate limit:
5 requests/minute on `gemini-3.5-flash`). This is fine for iteration but
will bottleneck a real demo.

**The hackathon itself provides $150 in Google Cloud credits** —
confirmed via the hackathon's own Devpost page (search-engine cache only;
`devpost.com` and `discuss.google.dev` are both blocked by this sandbox's
network egress proxy, so verify directly rather than trusting this
secondhand). Redemption: sign up for a no-cost Google Cloud trial, then go
to the **Resources tab** at `allthingsagentichackathon.devpost.com` and
fill out the credit form there to apply the $150 to that project's
billing account. Do this before Phase 4 — the same billing-enabled
project this unlocks is also exactly what Cloud Run + Vertex AI need, so
there's no reason to keep using the free-tier key once this is set up.
Two options once the credits land:
- Generate a new Gemini API key under the billing-enabled project (same
  `.env` shape as above, just a different key/no more rate limit), or
- Switch straight to real Vertex AI for the deployed Cloud Run service
  (the better target — one project, one credit pool, and it's what the
  hackathon's mandatory GCP-infra requirement expects anyway):

```
GOOGLE_GENAI_USE_VERTEXAI=TRUE
GOOGLE_CLOUD_PROJECT=<project id>
GOOGLE_CLOUD_LOCATION=us-central1
```

plus Application Default Credentials available to the process (a service
account attached to the Cloud Run revision, in production). No code
change needed either way — `agent.py` just passes the model name string
and the `google-genai` client resolves the backend from environment
variables at call time.

**Sentence-transformers note:** `verifiers/intent_graph` prefers a real
`sentence-transformers` embedding model but falls back automatically (and
loudly — a printed warning) to an offline scikit-learn hashing vectorizer
if the model can't download. In this sandbox, `huggingface.co` is
proxy-blocked (`403`), so it's currently running on the fallback —
**lexical, not semantic.** Check `intent_layer.extractor.embedding_backend()`
once deployed; Cloud Run's egress policy will determine which backend is
actually live there, and it changes IntentGraph's real detection behavior.

## 6. What's left

### 6.1 Cloud Run + Firestore (the only missing mandatory requirement)

Nothing built so far talks to GCP at all. Needed:

- **Cloud Run**: host `gate/quorum_gate.py`'s coordinator (wrap
  `retry_gate()` or `run_gate()` behind an HTTP endpoint — nothing like
  this exists yet, there's no web server/API layer anywhere in the repo).
- **Firestore**: replace two things currently held only in local
  process memory / local disk:
  - `IntentGraph` session state (`gate/quorum_gate.py::retry_gate`
    currently creates one `IntentGraph()` per process, in-memory only —
    it does not persist across invocations, and `IntentGraph` itself
    (`verifiers/intent_graph/intent_layer/graph.py`) has no
    serialization methods at all. **You will need to write that
    serialization** — nodes carry a `numpy.ndarray` embedding, which
    needs a real encoding strategy for Firestore (e.g. store as a list of
    floats).
  - Warden's audit log (`verifiers/warden/warden/audit.py::AuditLogger`)
    currently writes NDJSON to local disk at `.quorum/audit/` (path set
    in `gate/quorum_gate.py::DEFAULT_AUDIT_ROOT`). `AuditLogger` is a
    small, simple class (`log()`/`read()`) — either re-point its `root`
    at a Firestore-backed shim with the same interface, or write a
    parallel Firestore writer and call both/replace it. Given the
    project's own "coordinate, don't fuse" principle, prefer wrapping/
    replacing at the `AuditLogger` call sites in `quorum_gate.py` rather
    than modifying `verifiers/warden` itself.

### 6.2 One clean live end-to-end rep

`gate/quorum_gate.py::retry_gate()` (fresh Worker Agent call straight into
the gate, in one process) has not yet completed cleanly end-to-end in this
sandbox — it hit the free-tier rate limit once and a transient Gemini
`503` on immediate retry. Neither is a code bug. The identical gate logic
*is* validated (`gate/tests/test_quorum_gate.py::test_markdown_exfil_proposal_end_to_end`)
against a real, agent-generated proposal from an earlier successful live
run — so this is a confidence/demo-readiness gap, not an unknown. Re-run
it (with a billing-enabled key, ideally, to avoid the rate limit) before
the demo.

### 6.3 Applying a passed proposal

Nothing in this codebase actually applies a `PASS`ed proposal's diff to a
real target repo or opens a PR. `worker_agent/self_check.py::build_patch`
knows how to construct + apply a patch to a *temp copy* of Sentry for
self-checking, which is most of the mechanism needed — but doing this for
real (writing to a real target repo, committing, opening a PR) is new
scope, not yet started. Check whether the hackathon demo actually needs
this or whether "PASS verdict + visible provenance trail" is enough — the
original build plan's demo script (§7 below) only requires the PASS/REJECT
distinction to be visible, not a merged PR.

### 6.4 Demo, architecture diagram, writeup

Per the original 13-day plan's closing days — not started. The
`docs/INTEGRATION_MAP.md` + this handover + the code comments throughout
`gate/quorum_gate.py` and `worker_agent/` should cover most of what an
architecture diagram/writeup needs to say; the "Findings and learnings"
disclosures in §3 above (Warden's AUC, Review-Board's no-API constraint,
the Kernel ceiling-calc nuance) are exactly the kind of disclosure the
original build plan flagged as scoring well for Architectural Discipline
— carry them into the writeup rather than smoothing them over.

## 7. Running things

```bash
cd /path/to/Quorum
source worker_agent/.venv/bin/activate

# Worker Agent alone:
python -m worker_agent.cli "task description here"

# Full gate test suite (13 tests):
python -m pytest gate/tests/ -v

# Full pipeline, one call (Worker Agent -> gate, with retry):
python3 -c "
import sys; sys.path.insert(0, '.')
from gate.quorum_gate import retry_gate
proposal, gate_result, history = retry_gate('task description here')
print(gate_result.verdict, gate_result.reasons)
"
```

## 8. Standing constraints — do not "fix" these

- **Never call `warden.matcher`/`dual_compare` or trust its `tag`.**
  Confirmed inverted (AUC 0.332). Audit log only.
- **Never auto-invoke Review-Board.** It has no programmatic API by
  design. `ESCALATE` is the correct terminal state for anything that
  would otherwise need it.
- **Never modify verdict/threshold logic in `gate/pipeline.py`** without
  asking first — see §4.
- **Never copy `claim["origin"]` into a Kernel node's provenance
  verbatim** — that's the entire point of `determine_claim_origin()` in
  §3.3. If a future change makes this easier to bypass, that's a
  regression, not a simplification.
