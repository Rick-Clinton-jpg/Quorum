# Trust Boundary

A coordinator, not a fused system.

Trust Boundary sits in front of three independent, deterministic tools —
[Sentry](https://github.com/Rick-Clinton-jpg/Sentry) (input manipulation
detection), [Review-Board](https://github.com/Rick-Clinton-jpg/Review-Board)
(structured human deliberation), and
[Reasoning Kernel](https://github.com/Rick-Clinton-jpg/reasoning-kernel)
(claim validation) — and coordinates them with a lightweight pipeline
that logs decisions. It does not make them.

An earlier design tried to unify all three into a single shared
reasoning graph, with Reasoning Kernel enforcing sequencing across the
whole pipeline. That was rejected: Reasoning Kernel's rules are proven
for validating whether a reasoning graph's conclusions are supported by
evidence, not for generic workflow gating, and Review-Board is
deliberately never auto-triggered after adversarial testing showed an
ambient trigger misfiring. Repurposing either tool past its validated
boundary would launder unearned confidence into the result — exactly
what this project exists to prevent.

**These tools do not fuse into a single graph.** Each stays inside the
boundary it was actually built and tested for. See
[ARCHITECTURE.md](./ARCHITECTURE.md) for the full design and rationale.

## Source repos

- [Sentry](https://github.com/Rick-Clinton-jpg/Sentry) — input-stage
  manipulation detection
- [Review-Board](https://github.com/Rick-Clinton-jpg/Review-Board) —
  manually-invoked structured deliberation
- [Reasoning Kernel](https://github.com/Rick-Clinton-jpg/reasoning-kernel) —
  claim/evidence validation

## Status

`pipeline.py` is wired to both dependent tools' real APIs, confirmed
against their actual source rather than documented README/CLI behavior:

- Sentry: `from sentry import scan` — confirmed, including a live bug
  fix (`_highest_severity()` was filtering on `isinstance(f, dict)`,
  always `False` against real `Match` dataclass instances, so `REJECT`
  could never fire).
- Reasoning Kernel: wired against its real graph/transaction API
  (`grant`/`begin`/`add_node`/`add_edge`/`commit()`), not the assumed
  `Kernel.evaluate()`. `record_review_board_outcome()` builds each claim
  as a real graph and derives its own SUPPORTED/REFUTED/NEI/INCONCLUSIVE
  verdict, since the Kernel has no such concept natively.

Real end-to-end tests (`tests/test_pipeline.py`) run against the actual
installed `sentry` package. `CONTRIBUTING.md` documents the CI auto-fix
boundary for automated PR watchers.

Two items remain open, both tracked in detail in
[ARCHITECTURE.md](./ARCHITECTURE.md)'s Status/Open Items sections:

- No automated end-to-end test yet for the Reasoning Kernel side —
  blocked on that repo shipping packaging (`kernel` isn't pip-installable
  yet), not a defect here.
- `record_review_board_outcome()` hasn't been exercised against a real
  Review-Board run yet.
