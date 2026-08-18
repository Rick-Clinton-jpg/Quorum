"""Read-only ADK tools for studying Sentry's real source and test
conventions before drafting a patch. Scoped to verifiers/sentry/ only -
the Worker Agent has no write access and no access to any other verifier
in this phase."""

from __future__ import annotations

from pathlib import Path

_SENTRY_ROOT = (Path(__file__).resolve().parent.parent / "verifiers" / "sentry").resolve()


def list_sentry_files() -> list[str]:
    """List every file in the vendored Sentry repo, as paths relative to its root.

    Returns:
        Sorted relative paths, e.g. "rules/default_rules.json", "src/sentry/engine.py".
    """
    return sorted(
        str(p.relative_to(_SENTRY_ROOT))
        for p in _SENTRY_ROOT.rglob("*")
        if p.is_file() and ".git" not in p.parts
    )


def read_sentry_file(relative_path: str) -> str:
    """Read one file from the vendored Sentry repo.

    Args:
        relative_path: Path relative to the Sentry repo root, e.g.
            "rules/default_rules.json" or "tests/test_rules_detection.py".

    Returns:
        The file's contents, or an "error: ..." string if the path is
        missing or escapes the Sentry repo root.
    """
    candidate = (_SENTRY_ROOT / relative_path).resolve()
    if candidate != _SENTRY_ROOT and _SENTRY_ROOT not in candidate.parents:
        return f"error: {relative_path!r} is outside the Sentry repo root"
    if not candidate.is_file():
        return f"error: {relative_path!r} does not exist"
    return candidate.read_text()
