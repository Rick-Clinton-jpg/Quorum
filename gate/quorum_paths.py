"""Adds every vendored verifier's real import root to sys.path.

Mirrors exactly what docs/INTEGRATION_MAP.md documented as necessary in
Phase 1: Sentry is pip-installable (src layout) but not pip-installed in
this process, so its src/ has to be on sys.path directly; Reasoning
Kernel and IntentGraph aren't pip-installable at all (no
pyproject.toml/setup.py); Warden is pip-installable but, like Sentry,
isn't pip-installed here either. gate/pipeline.py itself is a flat
script, not a package (see its own pytest.ini: `pythonpath = .`), so
gate/ has to be on sys.path too for `import pipeline` to resolve the
same way its own tests already rely on.

Import this before importing pipeline, sentry, kernel, intent_layer, or
warden - the order among those doesn't matter, only that this runs first.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_ROOTS = [
    Path(__file__).resolve().parent,                  # gate/       -> `import pipeline`
    REPO_ROOT / "verifiers" / "sentry" / "src",        # ...src/sentry -> `import sentry`
    REPO_ROOT / "verifiers" / "reasoning_kernel",       # ...kernel.py  -> `import kernel`
    REPO_ROOT / "verifiers" / "intent_graph",           # ...intent_layer/ -> `import intent_layer`
    REPO_ROOT / "verifiers" / "warden",                 # ...warden/    -> `import warden`
]

for _root in _ROOTS:
    _root_str = str(_root)
    if _root_str not in sys.path:
        sys.path.insert(0, _root_str)
