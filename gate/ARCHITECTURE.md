# Trust Boundary: System Architecture

## Problem

AI systems chain multiple calls. Unsupported claims compound. Manipulation
enters silently through input. Decisions get made without deliberation.

Most attempts to guard against this ask a model to judge its own inputs,
its own reasoning, or its own outputs — a probabilistic system
adjudicating its own trust boundaries. That invites false confidence
rather than removing it.

## Approach

Three deterministic tools, each already built and independently validated:

| Stage         | Function                                    | Repo             |
|---------------|----------------------------------------------|------------------|
| Input         | Detect manipulation in raw text               | Sentry           |
| Deliberation  | Force structured review before action         | Review-Board     |
| Claim check   | Validate whether specific claims are supported | Reasoning Kernel |

## Design Decision: Coordinate, Don't Fuse

An earlier draft of this architecture tried to unify all three tools
through a shared reasoning graph — Sentry's findings and Review-Board's
approval stages would both be committed as nodes, with Reasoning Kernel
enforcing sequencing across the whole pipeline.

That was rejected, for two reasons:

1. **Reasoning Kernel's 8 rules validate whether a reasoning graph's
   conclusions are supported by evidence** — that's the job its 24-case
   evaluation actually tested. Using it as a generic state/sequencing
   gate ("does an APPROVE node exist?") stretches it past its proven
   boundary. It would probably work mechanically, but the confidence
   in the result would be borrowed from a different, unrelated
   validation — exactly the kind of unearned confidence this whole
   project exists to prevent.

2. **Review-Board is deliberately never auto-triggered.** Earlier
   testing showed an ambient/automatic trigger misfired, and the
   decision was made that an unreliable ambient gate is worse than no
   gate. Wiring Review-Board's stages into an automated pipeline
   directly reverses that decision.

**These tools do not fuse into a single graph.** They are independent
components, each staying inside the boundary it was actually built and
tested for, coordinated by a lightweight pipeline that logs decisions —
it does not make them.

## Pipeline

```
input text
    |
    v
Sentry.scan(text)
    |
    +-- Sentry's own severity verdict decides the action.
    |   (No new threshold is invented in the pipeline — the
    |    enforcement logic stays in the tool that owns it.)
    |
    +-- HIGH  --> REJECT, log, stop
    |
    +-- MEDIUM/LOW/NONE --> log, proceed
            |
            v
    PAUSE — human decides whether deliberation is warranted
            |
            v
    Review-Board run manually (/review-board in Claude Code)
    -- never triggered automatically --
            |
            v
    Review-Board produces specific claims/recommendations
            |
            v
    (optional) Reasoning Kernel validates *those specific claims*
    as FACT/CONCLUSION nodes — SUPPORTED / REFUTED / NEI / INCONCLUSIVE
    -- this is claim validation, not workflow gating --
            |
            v
    output + full provenance log (every stage, every decision, timestamped)
```

## Design Principles

1. **Deterministic over probabilistic** — no tool adjudicates its own
   inputs or outputs.
2. **Each tool stays inside its validated boundary** — no repurposing a
   tool proven for one job into a generic version of a different job.
3. **Repair/log over silent reject** — surface findings, don't just block.
4. **Explicit human approval required** — Review-Board is invoked by a
   person, never by the pipeline itself.
5. **Log everything** — every stage writes to a provenance record,
   whether or not it changes the outcome.

## Status

- [x] Sentry — built, published, tested
- [x] Reasoning Kernel — built, published, 24-case eval (16/16 + honest abstentions)
- [x] Review-Board — built, published, manual-invocation-only by design
- [x] Coordination pipeline skeleton (`pipeline.py`)
- [x] Real Sentry API wiring — confirmed against Sentry's actual source
      (`from sentry import scan`; `scan(text) -> list[Match]`). Confirming
      this surfaced a live bug: `_highest_severity()` filtered findings with
      `isinstance(f, dict)`, which is always `False` against real `Match`
      dataclass instances, so `REJECT` could never fire no matter what
      Sentry found. Fixed alongside the wiring.
- [x] Real Reasoning Kernel API wiring, at the code level — confirmed
      against `kernel.py` that there is no `Kernel.evaluate()`; the real
      API is a graph/transaction engine (`grant`/`begin`/`add_node`/
      `add_edge`/`commit()`). `record_review_board_outcome()` now builds
      each claim as a real graph and derives SUPPORTED / REFUTED / NEI /
      INCONCLUSIVE itself (see the comment above that function), since the
      Kernel has no such verdict concept natively.
- [x] End-to-end test with real Sentry calls (`tests/test_pipeline.py`) —
      a clean string and a confirmed-HIGH injection string, both run
      through the real `sentry` package.
- [ ] End-to-end test with real Reasoning Kernel calls — not yet possible
      in this repo: `kernel` isn't pip-installable (see Open Items), so
      `requirements.txt` deliberately leaves that dependency commented out
      rather than working around the gap here.
- [ ] `record_review_board_outcome()` exercised with a real Review-Board run

## Open Items

- **Reasoning-kernel packaging.** The reasoning-kernel repo has no
  `pyproject.toml` or `setup.py`, so it isn't pip-installable —
  `pip install git+https://github.com/Rick-Clinton-jpg/reasoning-kernel`
  currently installs nothing importable as `kernel`. `requirements.txt`
  leaves this dependency commented out until that repo ships packaging
  (tracked as a separate follow-up in that repo, not here). Until then,
  using the Kernel side of `pipeline.py` means manually putting a
  `reasoning-kernel` checkout's repo root on `PYTHONPATH`.
- **Verdict derivation lives in the pipeline, not the Kernel.** The
  SUPPORTED/REFUTED/NEI/INCONCLUSIVE (plus ABSTAIN/ERROR) mapping in
  `record_review_board_outcome()` is application logic layered on top of
  a general-purpose graph engine — the Kernel guarantees none of it. If
  reasoning-kernel's rules change, re-verify this mapping against the
  comment above that function.
- No automated test yet exercises the Kernel side of the pipeline (see
  Status) — add one once reasoning-kernel is pip-installable.
