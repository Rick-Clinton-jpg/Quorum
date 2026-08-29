"""Agent Identity: per-agent API keys, resolved to an agent_id that
flows into every audit.log() call.

Before this module, every proposal that reached the gate - regardless of
which agent, human, or script actually called the service - was logged
under one hardcoded agent_id ("quorum-worker-agent"). That made the
audit trail useless for answering "which caller did this" once more than
one caller exists, which defeats the point of an append-only audit log
for a Fortified Enterprise Fleet.

Keys are configured via the QUORUM_AGENT_KEYS env var, a JSON object
mapping API key -> agent_id, e.g.:

    QUORUM_AGENT_KEYS='{"qk_live_abc123...": "quorum-worker-agent-prod"}'

Deliberately NOT Secret Manager for this: these are per-agent identity
tokens meant to be issued to multiple callers and rotated independently,
not one shared deploy-time credential - a JSON map in an env var (set
via --update-env-vars, or --update-secrets if the deployer wants it
Secret-Manager-backed too) is the right shape for that, and matches how
the GitHub PR token already proved out the Secret Manager path
separately in this project.

If QUORUM_AGENT_KEYS is unset entirely, auth is OFF (every request is
accepted as the default agent_id) - this keeps local dev and the
existing test suite working with zero setup. Once it's set, every
request to a gated endpoint MUST carry a valid key.
"""

from __future__ import annotations

import json
import os
from typing import Optional

DEFAULT_AGENT_ID = "quorum-worker-agent"
API_KEY_HEADER = "X-Quorum-Agent-Key"


class InvalidAgentKey(Exception):
    """Raised when auth is configured and the caller's key doesn't match."""


def _load_key_map() -> Optional[dict[str, str]]:
    raw = os.environ.get("QUORUM_AGENT_KEYS")
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"QUORUM_AGENT_KEYS is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict) or not all(isinstance(v, str) for v in parsed.values()):
        raise ValueError("QUORUM_AGENT_KEYS must be a JSON object of {api_key: agent_id}")
    return parsed


def resolve_agent_id(api_key: Optional[str]) -> str:
    """Returns the agent_id for `api_key`, or raises InvalidAgentKey.

    Auth is only enforced if QUORUM_AGENT_KEYS is set at all - see this
    module's docstring. Re-reads the env var on every call (cheap: one
    small JSON parse) rather than caching at import time, so a deploy
    that updates the env var takes effect without a code change.
    """
    key_map = _load_key_map()
    if key_map is None:
        return DEFAULT_AGENT_ID

    if not api_key or api_key not in key_map:
        raise InvalidAgentKey(
            f"missing or unrecognized {API_KEY_HEADER} header - this deployment requires agent authentication"
        )
    return key_map[api_key]
