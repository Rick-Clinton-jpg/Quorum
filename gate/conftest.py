"""Ensures every vendored verifier's real import root is on sys.path
BEFORE any test module in this directory is collected.

This matters because pipeline.py resolves its `from sentry import ...`
and `from kernel import ...` in a module-level try/except, evaluated
exactly once at first import and cached in sys.modules from then on. If
gate/tests/test_pipeline.py (the vendored Trust Boundary test, which only
needs sentry on sys.path) gets collected before gate/tests/test_quorum_gate.py
(which needs kernel/intent_layer/warden too, via quorum_paths.py), pytest's
alphabetical collection order would import `pipeline` with kernel not yet
resolvable - permanently caching Kernel = None for the rest of the
session, regardless of quorum_paths.py running later. A conftest.py in
this directory is guaranteed to load before any test module here, which
is what actually fixes this reliably (module-file import order between
two test files is not something to depend on for this).
"""

import quorum_paths  # noqa: F401 - gate/ itself is already on sys.path via pytest.ini's pythonpath=.
