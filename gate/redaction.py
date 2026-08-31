"""Typed-pattern redaction for text that might reach a public surface -
the /audit/trail endpoint, OTel span attributes, GitHub PR bodies, and
API error responses.

Deliberately NOT an entropy/long-random-string scrubber. An earlier
draft of this fix considered exactly that (redact anything that "looks
like a secret" by length/randomness) and it was correctly rejected: it
would destroy legitimate commit hashes, trace IDs, and UUIDs that
routinely appear in diffs and rationale text and are useful evidence,
not something to hide. Only known PII shapes are redacted here - the
same shapes verifiers/sentry's own pii_exposure_pattern rule matches
(verifiers/sentry/rules/default_rules.json), duplicated as a literal
rather than imported from that vendored, JSON-defined ruleset, since
this needs to run in places (audit reads, span attributes, PR bodies)
that have nothing to do with Sentry's own scan pipeline.
"""

from __future__ import annotations

import re

_PII_PATTERN = re.compile(
    r"(\b\d{3}-\d{2}-\d{4}\b"
    r"|\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
    r"|\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{1,4}\b"
    # Both the local and domain parts require a "dot" group. A local
    # part of `*` (0-or-more) instead of `+` allows a bare word ("out")
    # right before an EARLIER, unrelated "at" in the surrounding
    # sentence to satisfy the whole pattern by itself - confirmed live:
    # against "reach out at jane dot doe at example dot com", `*` matched
    # "out at jane dot doe" and left "example dot com" (the real domain)
    # completely unredacted. Narrower on purpose: this only catches a
    # spelled-out local part shaped like "word dot word", not a bare
    # single-word local part ("jane at example dot com") - that's a
    # smaller, disclosed gap, not a silent one.
    r"|\b[a-z0-9_]+(?:\s+dot\s+[a-z0-9_]+)+\s+at\s+[a-z0-9_]+(?:\s+dot\s+[a-z0-9_]+)+\b)",
    re.IGNORECASE,
)


def scrub_pii(text: str) -> str:
    """Replaces any PII-shaped substring (a formatted or spelled-out
    email, an SSN, a credit-card-formatted number) with a fixed
    placeholder. Safe to call on arbitrary text, including None/empty -
    only touches text matching one of these specific shapes, so a commit
    SHA, trace ID, or UUID passes through untouched."""
    if not text:
        return text
    return _PII_PATTERN.sub("[REDACTED]", text)
