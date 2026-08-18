# Changelog

## Rule 3 confidence-ceiling fix — FACT nodes now require valid confidence

FACT nodes could previously be constructed (and loaded) with
`confidence=None`. Rule 3's aggregation step (`Kernel._cap`) treated that
missing value as zero support rather than flagging it, so any CONCLUSION or
HYPOTHESIS resting on that FACT got silently capped at 0.0 — regardless of
how well-supported it actually was. Surfaced during Agent-Kernel Bridge
integration work (a separate discovery path from the real-model benchmark
run described in the README).

Fixed with two layers:

- `Node.__post_init__` now hard-rejects a FACT node at construction time if
  its confidence is `None` or outside `0.0-1.0`, with an error naming the
  node and where to source the value from.
- `Kernel._cap()` adds a defense-in-depth check: if it ever encounters a
  FACT support node with `confidence=None` (e.g. a bypass via mutation or a
  deserialized graph), it raises loudly instead of silently excluding it
  from the ceiling calculation.

See `test_kernel.py` for the regression tests covering this. Merged in
[PR #1](https://github.com/Rick-Clinton-jpg/reasoning-kernel/pull/1).
