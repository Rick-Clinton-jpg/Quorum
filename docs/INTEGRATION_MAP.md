# INTEGRATION_MAP.md

Produced by reading the actual vendored source of all six repos under
`gate/` and `verifiers/` — not READMEs, not CLI docs, not assumptions.
Every signature below was read directly from the `.py` file cited.
Where a claim is inferred rather than read verbatim, it's marked
`[INFERRED]`; everything else is `[CONFIRMED]` against the cited file/line.

No integration code has been written yet. This document is the input to
that work, not the output of it.

---

## 0. Headline findings (read this first)

1. **Trust Boundary (`gate/`) already did most of Phase 1's job for two of
   its three dependencies.** `gate/pipeline.py` and `gate/ARCHITECTURE.md`
   document, in detail, two real historical bugs from assumed APIs:
   `_highest_severity()` filtered on `isinstance(f, dict)` against Sentry's
   real `Match` **dataclass** instances (always `False`, so `REJECT` could
   never fire), and an assumed `Kernel.evaluate()` that doesn't exist —
   the real Reasoning Kernel API is a graph/transaction engine. Both are
   fixed in the vendored copy. This report re-confirms both against the
   underlying source independently (Sections 2–3) rather than trusting
   `gate/`'s own account, and both check out.

2. **Review-Board has zero programmatic API.** It is not a Python
   package — there is no `.py` file anywhere in the repo. It's a Claude
   Code **skill** (`SKILL.md`, `disable-model-invocation: true`),
   invoked only by a human typing `/review-board` in a Claude Code
   session. Any "integration" with it is a slash-command instruction to
   a human/Claude Code operator, not a function call. `gate/pipeline.py`
   already reflects this correctly (`pause_for_review_board()` just
   pauses and logs; nothing calls Review-Board programmatically). Any
   Worker Agent design that assumes a `review_board.run(...)` function
   will fail — there is nothing to call.

3. **Half the fleet isn't pip-installable.** `reasoning-kernel` and
   `intent-layer` (IntentGraph) both have **no `pyproject.toml` and no
   `setup.py`**. They only work via `PYTHONPATH`/`sys.path` manipulation
   pointing at a repo checkout. `review-board` isn't a Python package at
   all (see #2). Only `sentry` and `warden` are real installable
   packages. Any Cloud Run image that needs `kernel` or `intent_layer`
   importable has to vendor the source onto `PYTHONPATH` directly
   (which is exactly what this repo now does under `verifiers/`) — `pip
   install git+https://...` will not work for those two.

4. **Warden's core drift detector is confirmed inverted by its own
   README, not just by the build-plan's risk note.** `AUC = 0.332` on a
   60-pair hand-labelled set — worse than the 0.50 random baseline — is
   stated explicitly in `verifiers/warden/README.md` under "Known
   Issues / Limitations," and the flawed code path (`matcher.dual_compare`,
   word-Jaccard vs. objective) is confirmed in `warden/matcher.py`. Per
   the build plan: **do not gate on Warden's tag/score.** Use only
   `warden.audit.AuditLogger.log()` for the append-only audit trail —
   see Section 6.

5. **License mix is inconsistent across the fleet.** `gate` (trust-boundary),
   `sentry`, `reasoning-kernel`, and `review-board` all carry PolyForm
   Noncommercial 1.0.0. `intent-layer` and `warden` ship **no LICENSE file
   at all** (confirmed by directory listing — not merely unread). Worth a
   decision before any public writeup/repo is published, since default
   copyright (all rights reserved) applies to the two unlicensed repos.

6. **No dependency-version conflict was found**, but only because most of
   the fleet doesn't declare one. `sentry` requires Python `>=3.9`,
   `warden` requires `>=3.10` — compatible (use `>=3.10` as the floor).
   `reasoning-kernel` and `gate` declare no Python floor at all (no
   packaging file). `intent-layer`'s `requirements.txt` pins **no
   versions** (`sentence-transformers`, `numpy`, `scikit-learn` with no
   `>=`), unlike every other repo's requirements file — worth pinning
   before this becomes a real dependency of the Worker Agent's
   environment.

---

## 1. Trust Boundary — `gate/`

**What it is:** the coordinator itself (per the build plan's component
map, this repo *is* the Trust Boundary / gate service, not a verifier).
Not a pip package (no `pyproject.toml`/`setup.py`) — a script + tests
meant to be run in place.

**Entry points** — `gate/pipeline.py`:

| Function | Signature | Returns |
|---|---|---|
| `run_input_stage` | `run_input_stage(text: str)` | `PipelineResult` — runs Sentry only |
| `pause_for_review_board` | `pause_for_review_board(result: PipelineResult, reason: str)` | `PipelineResult`, action set to `PAUSE_FOR_REVIEW_BOARD` |
| `record_review_board_outcome` | `record_review_board_outcome(result: PipelineResult, claims: list)` | `PipelineResult`, `.claim_validation` populated via Reasoning Kernel |
| `run` | `run(text: str)` | `PipelineResult` — full entry point: Sentry, then pause (never auto-runs Review-Board or the Kernel) |

**Types** (dataclasses, `gate/pipeline.py`):
- `PipelineResult(action: PipelineAction, findings: list, provenance: list, claim_validation: Optional[dict])`
- `PipelineAction` enum: `REJECT` / `PROCEED_WITH_LOG` / `PAUSE_FOR_REVIEW_BOARD`
- `Severity` enum (gate's own, separate from Kernel's `Severity`): `HIGH`/`MEDIUM`/`LOW`/`NONE`
- `ProvenanceEntry(stage: str, timestamp: str, detail: dict)`

**Confirmed behavior:**
- Imports `from sentry import scan as sentry_scan_raw` — soft-fails to
  `None` if not installed (results in `PROCEED_WITH_LOG` with a logged
  error, not a crash).
- Imports `from kernel import Kernel, Node, Edge, Provenance, Confidence,
  NodeType, EdgeType, Origin, Derivation, Perm, KernelReject` — same
  soft-fail pattern.
- `run()` **never calls Review-Board or the Kernel automatically.** Only
  Sentry runs unconditionally. The Kernel only runs if a caller
  explicitly invokes `record_review_board_outcome()` with claims a human
  produced after running `/review-board` themselves.
- `DECISION_THRESHOLD = 0.60` is a constant **mirrored, not imported**,
  from `reasoning_kernel/eval_harness.py` (`eval_harness.py` is a
  script, not part of the `kernel` package) — a real drift risk if that
  script's constant ever changes without this being updated by hand.
- The claim schema `record_review_board_outcome()` expects is documented
  in the function's docstring in full (nodes/edges JSON shape) — reused
  verbatim from `eval_harness.GraphSpec`/`ground_truth_cases.py` rather
  than invented.
- `CONTRIBUTING.md` sets an explicit CI auto-fix boundary: automated PR
  watchers may fix mechanical failures (lint, imports, types) but must
  **stop and ask** before touching `record_review_board_outcome()`,
  `_highest_severity()`, or `DECISION_THRESHOLD`/any verdict mapping.
  Relevant if we wire an automated retry loop against this repo later.

**Dependencies** (`gate/requirements.txt`): `git+https://github.com/Rick-Clinton-jpg/Sentry`
(active). Reasoning Kernel line is present but **commented out** —
explicitly, because it isn't pip-installable (see Section 3). No Python
version floor declared.

---

## 2. Sentry — `verifiers/sentry/`

**What it is:** a real, pip-installable package (`sentry-detection-engine`,
`pyproject.toml`, `requires-python = ">=3.9"`). Src layout under `src/sentry/`.

**Public API** (`verifiers/sentry/src/sentry/__init__.py`):
```python
from sentry.engine import Match, Rule, load_rules, scan
```

| Symbol | Signature | Notes |
|---|---|---|
| `scan` | `scan(text: str, rules: list[Rule] \| None = None) -> list[Match]` | Loads default rules if `rules` omitted |
| `load_rules` | `load_rules(path: str \| Path = DEFAULT_RULES_PATH) -> list[Rule]` | Raises `RuleLoadError` on malformed JSON/regex/severity |
| `Match` | frozen dataclass: `rule: str, severity: str, description: str, start: int, end: int, text: str` | **Not a dict** — `gate/pipeline.py`'s historical bug came from assuming it was |
| `Rule` | frozen dataclass: `name: str, pattern: str, severity: str, description: str, regex: regex.Pattern` | see note below — not stdlib `re.Pattern` |
| `RuleLoadError(ValueError)` | — | |

`severity` values are plain strings `"HIGH"`/`"MEDIUM"`/`"LOW"` (validated
against `VALID_SEVERITIES` at load time, not an enum at the `Match` level).

**Substantively modified for Quorum, not vendored as-is:** `engine.py`
was switched from stdlib `re` to the third-party `regex` module, and
`scan()` now enforces a real `timeout=1.0s` on every rule's
`finditer()` call. `rules/default_rules.json` is a file the Worker
Agent proposes diffs to (see `gate/tests/fixtures/escalate_demo_proposal.json`),
so a rule's pattern is untrusted input, not maintainer-reviewed —
stdlib `re` has no way to bound a catastrophic-backtracking match's
wall-clock time, and could hang `scan()` (and therefore the whole
gate) indefinitely on real input. A rule that exceeds its budget fails
closed: it's reported as its own HIGH-severity finding ("unsafe to
evaluate") rather than silently skipped or left to hang, and other
rules in the same scan still run. See `verifiers/sentry/tests/test_redos_defense.py`
for a real (not mocked) catastrophic-backtracking pattern proving the
timeout actually bounds the match.

**CLI** (`verifiers/sentry/src/sentry/cli.py`, entry point `sentry` via
`pyproject.toml`'s `[project.scripts]`):
```
sentry scan [TARGET] [--stdin] [--rules PATH]
```
`TARGET` is text or a file path. Exit code `1` if any `HIGH` finding,
else `0`; `2` on a rules-load error.

**Dependencies:** `regex>=2023.0.0` (see above), plus stdlib `json`,
`pathlib`, `dataclasses`, `argparse`. Dev-only: `pytest>=7.0`.
`requires-python = ">=3.9"`. `gate/quorum_paths.py` puts
`verifiers/sentry/src` on `sys.path` directly rather than
pip-installing this package, so `regex` is also listed explicitly in
`service/requirements.txt` for the same reason that file already lists
`pytest` — see that file's own comment.

---

## 3. Reasoning Kernel — `verifiers/reasoning_kernel/`

**What it is:** a deterministic graph/transaction engine implementing 8
integrity rules (TLOR Document 9). **Not pip-installable** — no
`pyproject.toml`, no `setup.py`. `kernel.py` itself is pure stdlib
(`copy`, `time`, `dataclasses`, `enum`, `typing`) so it works fine on
`PYTHONPATH`, it's just not a packaged distribution.

**There is no `Kernel.evaluate()`.** Confirmed by reading the full class
body of `Kernel` in `kernel.py` — the real public surface is:

| Method | Signature | Notes |
|---|---|---|
| `__init__` | `Kernel(propagation: str = "min")` | `"min"` or `"product"` confidence-propagation mode |
| `grant` | `grant(module: str, perms: set)` | Grants a module a set of `Perm` values |
| `begin` | `begin(module: str)` | Opens a transaction (snapshots nodes/edges); raises `RuntimeError` if one is already open |
| `add_node` | `add_node(module: str, node: Node)` | Requires `Perm.WRITE`; raises `ValueError` on duplicate id |
| `add_edge` | `add_edge(module: str, edge: Edge)` | Requires `Perm.WRITE` |
| `update_node` | `update_node(module: str, node_id: str, **changes)` | Rule 4 (provenance immutable) / Rule 7 (id immutable) enforced here |
| `delete_node` | `delete_node(module: str, node_id: str)` | Requires `Perm.DELETE`; becomes `state=ARCHIVED`, never truly deletes (Rule 8); refuses via `KernelReject` if node participates in a `CONTRADICTS` edge (Rule 6) |
| `validate` | `validate() -> list[Violation]` | Runs all 8 rules, does not mutate |
| `commit` | `commit() -> list[Violation]` | Validates; any `REJECT` → rollback + raise `KernelReject(rejects)`; `REPAIR` violations applied in place; returns all violations found |
| `rollback` | `rollback(reason: str = "")` | Restores pre-`begin()` snapshot |
| `trace` | `trace(node_id: str, depth: int = 0) -> str` | Human-readable Rule-5 justification trace |

**Types:**
- `Node(id: str, type: NodeType, label: str, state: State = DRAFT, provenance: Optional[Provenance] = None, confidence: Optional[Confidence] = None, contested: bool = False)` — `__post_init__` **raises `FactConfidenceError`** if `type is FACT` and `confidence is None`, or confidence outside `[0.0, 1.0]`.
- `Edge(src: str, dst: str, type: EdgeType)`
- `Provenance(source: str, origin: Origin, timestamp: float = time.time())`
- `Confidence(value: float, derivation: Derivation, refs: list = [])`
- `Violation(rule: int, severity: Severity, target: str, message: str)` — `Severity` here is `Kernel`'s own enum: `REJECT`/`REPAIR`/`WARN` (do not confuse with `gate/pipeline.py`'s unrelated `Severity` enum, or Sentry's plain-string severities)
- `KernelReject(Exception)` — `.violations: list[Violation]`
- `FactConfidenceError(ValueError)`

**Enums:** `NodeType` (11 values incl. `FACT`, `CONCLUSION`, `ASSUMPTION`,
`UNKNOWN`…), `EdgeType` (10 values incl. `SUPPORTS`, `CONTRADICTS`,
`DEPENDS_ON`, `INCONCLUSIVE`…), `State` (`DRAFT`/`PENDING`/`VERIFIED`/
`LOCKED`/`ARCHIVED`), `Perm` (`READ`/`WRITE`/`VERIFY`/`ARCHIVE`/`LOCK`/
`DELETE`), `Origin` (6 values — `FACT_ORIGINS = {RETRIEVED, USER_INPUT,
VERIFIED}` is the allow-list Rule 1 checks; `GENERATED`/`INFERRED`/
`REPORTED` get REPAIR-demoted), `Derivation` (`EVIDENCE`/`REASONING`/
`VERIFICATION`/`ASSERTED` — `ASSERTED` is illegal, Rule 3 REJECTs it).

**The 8 rules** (`kernel.py` `validate()`, cross-checked against
`KERNEL.md`'s table — they match):

| # | Rule | Severity |
|---|---|---|
| 1 | FACT with no/unverifiable provenance | REPAIR (demoted to ASSUMPTION) |
| 2 | UNKNOWN carrying a confidence value | REJECT |
| 3 | confidence `ASSERTED`, out of `[0,1]` range, or exceeds support ceiling | REJECT / REPAIR |
| 4 | provenance stripped | REJECT (raised directly in `update_node`, not `validate()`) |
| 5 | CONCLUSION with no grounding path | REJECT |
| 6 | contradicted node not marked `contested` | REPAIR; deletion of a contradicted node REJECTed |
| 7 | node id changed | REJECT (raised directly in `update_node`) |
| 8 | dangling edge reference / SUPPORTS-DEPENDS_ON cycle | REJECT |

**No CLI.** `kernel_demo.py` is a runnable demonstration script
(`python kernel_demo.py`), not an installed entry point.

**Dependencies** (`requirements.txt`): stdlib only for `kernel.py` itself.
`requests`, `openai`, `google-generativeai` are only needed by
`run_real_eval.py` (the eval harness), not by the kernel or by
`gate/pipeline.py`'s usage of it.

---

## 4. Review-Board — `verifiers/review_board/`

**What it is: not a Python package. There are zero `.py` files in this
repo** (confirmed by directory listing — `SKILL.md`, `README.md`,
`LICENSE`, `reports/*.md` only). It is a **Claude Code skill**:
`SKILL.md` frontmatter sets `disable-model-invocation: true`, meaning it
does not even auto-trigger inside Claude Code — it only runs when a
human explicitly types `/review-board`.

**"API surface":** none, programmatically. The six stages (restate
objective → map solution paths → prior-art check w/ mandatory second
independent search → score against disclosed criteria → six-angle
multi-perspective review → stop for explicit human approval) are a
prompt protocol a Claude Code session follows when invoked, not
functions with inputs/outputs. `README.md` explains *why*: earlier
versions tried ambient auto-triggering and adversarial testing found it
unreliable in two distinct ways (losing to more specific sibling skills,
misfiring on its own skip clause) — so v3 deliberately removed
auto-triggering rather than keep tuning it.

**Consequence for the build plan:** `gate/pipeline.py`'s
`pause_for_review_board()` is the only correct integration shape — it
sets `PAUSE_FOR_REVIEW_BOARD` and logs, then waits for a human to run
`/review-board` out-of-band and separately call
`record_review_board_outcome()` with the resulting claims. **Do not
design the Worker Agent's retry loop to assume Review-Board can be
invoked as a subroutine of an automated pipeline** — it structurally
cannot be, by design, and re-enabling auto-invocation would reverse a
decision this repo already tested and rejected.

**License:** PolyForm Noncommercial 1.0.0.

---

## 5. IntentGraph — `verifiers/intent_graph/` (source repo: `intent-layer`)

**What it is:** conversation-level re-entry/drift detection. **Not
pip-installable** — no `pyproject.toml`/`setup.py`, same gap as
Reasoning Kernel. Package lives at `verifiers/intent_graph/intent_layer/`.

**Public API** (`intent_layer/__init__.py`):
```python
from intent_layer import IntentExtractor, IntentGraph, IntentNode, Edge, score_node, RiskResult
```

| Symbol | Signature | Notes |
|---|---|---|
| `IntentExtractor.extract` | `extract(self, text: str) -> dict` | keys: `description, embedding, domain, confidence, is_reformulation_cue, is_backreference_cue` |
| `IntentGraph.add_turn` | `add_turn(self, text: str, timestamp: int) -> Optional[IntentNode]` | Returns `None` for bracketed marker turns (e.g. `[SAFETY BOUNDARY TRIGGERED]`), which mutate graph state but create no node |
| `IntentGraph.run_conversation` | `run_conversation(self, turns: List[str]) -> List[IntentNode]` | Batch entry point |
| `IntentGraph.lineage_boundary_nodes` | `lineage_boundary_nodes(self, node: IntentNode) -> List[IntentNode]` | |
| `score_node` | `score_node(node: IntentNode, graph: IntentGraph) -> RiskResult` | `RiskResult.risk` is `"LOW"`/`"MEDIUM"`/`"HIGH"` |

**Hard gate (confirmed in `scorer.py`):** risk can never exceed `LOW`
unless the node's lineage contains a real prior `safety_boundary=True`
node — similarity or reformulation language alone never elevates risk.
This is checked first and short-circuits the rest of the score formula.

**Types:** `IntentNode` (dataclass: `intent_id, description, embedding:
np.ndarray, timestamp, confidence, direction, safety_boundary,
parent_intent, domain, lineage_root, is_reformulation_cue,
is_backreference_cue`), `Edge(source, target, edge_type)`, `RiskResult
(node_id, risk, score, components: dict, explanation: str)`.

**Disclosed limitation (own README, confirmed, re-tested on a second
machine, and confirmed again on the deployed Cloud Run service):** the
embedding backend defaults to an offline scikit-learn `HashingVectorizer`
- a deliberate default as of Phase 4, not a network fallback anymore.
It used to try `sentence-transformers`/`all-MiniLM-L6-v2` first on every
cold start; measured live, that cost ~6.6s per cold worker (~6s of it is
just `import sentence_transformers`, which pulls in torch), paid in full
even though the network call that follows always failed under this
deployment's real egress policy (`403` on `huggingface.co`, a policy
block, not a hypothetical). Going straight to hashing dropped that to
~0.07s with the eval's 15/15 result unchanged (the fallback is
deterministic, and was already what every prior eval run actually used).
Set `QUORUM_INTENT_EMBEDDING_BACKEND=sentence-transformers` to opt back
into the real model in an environment where it's actually reachable.
`extractor.embedding_backend()` reports which backend is actually active
at runtime — **check this in any environment IntentGraph runs in**
(including Cloud Run), since the hashing fallback is lexical, not
semantic, and changes the tool's real detection behavior.

Also disclosed: escalation-score signal is weak (confidence for a
keyword-confirmed domain match is floored at 0.85, so two turns naming
the same restricted domain don't register as "escalating" relative to
each other); eval set is 15 hand-written synthetic trajectories, not
adversarially tested at scale.

**No CLI**, no packaging. `demo.py` and `eval/run_eval.py` are runnable
scripts, not installed entry points.

**Dependencies** (`requirements.txt`): `sentence-transformers`, `numpy`,
`scikit-learn` — **no version pins**, unlike every other repo's
requirements file. No Python floor declared.

---

## 6. Warden — `verifiers/warden/`

**What it is:** a real, pip-installable package (`warden-agent`, `setup.py`,
`python_requires=">=3.10"`, `install_requires=["click>=8.1.0"]`). CLI
entry point `warden` → `warden.cli:main`.

**Per the build plan: only the audit-log function is in scope for
Quorum.** The drift-tag/matching function is confirmed broken by the
repo's own README (Section 0.4) and must not gate anything.

### 6a. Audit log (the part we use) — `warden/audit.py`

| Symbol | Signature | Notes |
|---|---|---|
| `AuditLogger.__init__` | `AuditLogger(root: Path \| str = ".warden")` | creates `<root>/audit/` |
| `AuditLogger.log` | `log(self, *, agent_id: str, objective: str, status: str, tag: str, note: str, action: str = "NONE", extra: Optional[dict] = None) -> Path` | Appends one NDJSON record to `<root>/audit/YYYY-MM-DD.ndjson`; returns the file path written |
| `AuditLogger.read` | `read(self, *, session: Optional[str] = None, tag: Optional[str] = None, since: Optional[datetime] = None, limit: int = 200) -> list[dict]` | Reads back across all `*.ndjson` files, newest first |

Append-only by construction (opens with `"a"`, never truncates/rewrites).
This is the shape to call from the gate's provenance logging — pass
whatever `tag`/`note` Quorum's own quorum-vote produces as free-text
fields; **do not source `tag` from `warden.matcher`**.

### 6b. Drift matcher — `warden/matcher.py` (confirmed broken, do not use for gating)

`dual_compare(status: str, objective: str) -> DualResult` runs two
correlated Jaccard raters (word-stem overlap, character-trigram overlap;
Pearson r = 0.934 between them per the README, i.e. not independent) and
tags `MATCH`/`DRIFT`/`UNCLEAR`/`DIVERGENT`. Confirmed present and
importable, confirmed to be the exact mechanism the README's AUC=0.332
disclosure refers to (vocabulary overlap, not directional alignment —
e.g. "deleting the endpoints" and "building the endpoints" can score
similarly). **Not used by Quorum's gate logic**, per the build plan —
noted here only so nobody re-derives this by trial and error later.

### 6c. Orchestration — `warden/core.py` (not needed for Quorum's log-only usage, noted for completeness)

`Warden(root: Path | str = ".warden")` wraps session registry + polling +
`AuditLogger` + `matcher.dual_compare` together (`watch()`, `check_once()`,
`check_all()`, `run_loop()`, `run_daemon()`). Since Quorum only wants the
audit-log function, **instantiate `AuditLogger` directly** rather than
going through `Warden`/`check_once()`, to avoid pulling in the matcher
call Section 6b says not to trust.

**License:** none — no LICENSE file present in the repo (confirmed by
directory listing).

---

## 7. Cross-repo dependency/version summary

| Repo | Pip-installable? | Python floor | Runtime deps | License |
|---|---|---|---|---|
| `gate` (trust-boundary) | No (script) | none declared | `sentry` (git) | PolyForm NC 1.0.0 |
| `sentry` | **Yes** | `>=3.9` | stdlib only | PolyForm NC 1.0.0 |
| `reasoning_kernel` | **No** | none declared | stdlib only (`kernel.py`); `requests`/`openai`/`google-generativeai` for eval scripts only | PolyForm NC 1.0.0 |
| `review_board` | N/A — not Python | N/A | N/A (Claude Code skill) | PolyForm NC 1.0.0 |
| `intent_graph` | **No** | none declared | `sentence-transformers`, `numpy`, `scikit-learn` (**unpinned**) | **none (no LICENSE file)** |
| `warden` | **Yes** | `>=3.10` | `click>=8.1.0` | **none (no LICENSE file)** |

No hard version conflict exists today, but the effective floor for a
combined environment is Python `>=3.10` (Warden's requirement, the
strictest declared one). `reasoning_kernel` and `intent_graph` must be
put on `PYTHONPATH` (already done — they live under `verifiers/` in this
repo) rather than `pip install`ed.

---

## 8. What this means for Phase 2 (not yet started)

- The Worker Agent's proposed patch + rationale is what gets scanned.
  Sentry (`scan(text) -> list[Match]`) is the only verifier with an
  unconditional automatic gate today — the gate's existing `REJECT` on
  `HIGH` severity is real and confirmed.
- Reasoning Kernel validates specific claims *as claims*, not
  free-form text — Phase 2 needs a translation step from "the agent's
  stated rationale" into the `{nodes, edges}` claim schema
  `gate/pipeline.py` already documents, before the Kernel can check
  anything.
- Review-Board cannot be wired into an automated retry loop — any
  six-stage deliberation step is a human-in-the-loop checkpoint, not
  code to call. If the build plan's "Review Board — six-stage
  deliberation on the proposal" pillar needs to run unattended for a
  demo, that's a real design gap to resolve explicitly, not an
  integration detail.
- IntentGraph's cross-session drift check depends on which embedding
  backend is actually live in the deployment environment (Cloud Run's
  egress policy will determine whether it's the real
  sentence-transformer or the offline hashing fallback) — worth
  checking `intent_layer.extractor.embedding_backend()` once deployed,
  since the two backends have materially different detection behavior.
- Warden's role is exactly what the build plan says and no more:
  `AuditLogger.log()` as the append-only trail. Nothing here should call
  `warden.matcher` or read `DualResult.final_tag` as a signal.
