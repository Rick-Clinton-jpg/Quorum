# Contributing

## CI auto-fix boundary (for Claude Code / automated PR watchers)

An automated session watching a PR on this repo (fixing CI failures, responding
to review comments) may fix, without asking first:

- Linting and formatting failures
- Import errors (missing imports, unused imports, import ordering)
- Type-checking noise (annotations, type-checker configuration)
- Other purely mechanical failures that don't change what the code decides

It may **not** modify, without stopping and asking the repo owner first:

- Any logic inside `record_review_board_outcome()` — the claim schema, the
  graph-building step, or the SUPPORTED/REFUTED/NEI/INCONCLUSIVE/ABSTAIN/ERROR
  verdict derivation (see the comment above that function in `pipeline.py`
  for which parts of that mapping are confirmed by instruction versus
  inferred from reading reasoning-kernel's source)
- Any logic inside `_highest_severity()`
- `DECISION_THRESHOLD`, or any other threshold, verdict, or mapping decision
  anywhere in `pipeline.py`

This applies even if a test is failing *because* of one of the above — a
failure that traces back to adapter/verdict logic gets reported, with the
failure and the suspected cause, and then the session waits. It does not get
silently "fixed" to make CI green.

If it's unclear which side of this line a given CI failure falls on, treat it
as governed logic (ask first), not as mechanical.
