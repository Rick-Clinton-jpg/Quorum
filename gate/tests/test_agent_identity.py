"""Tests for gate/agent_identity.py - per-agent API key resolution."""

from __future__ import annotations

import pytest

from gate.agent_identity import DEFAULT_AGENT_ID, InvalidAgentKey, resolve_agent_id


def test_auth_off_by_default_returns_default_agent_id(monkeypatch):
    monkeypatch.delenv("QUORUM_AGENT_KEYS", raising=False)
    assert resolve_agent_id(None) == DEFAULT_AGENT_ID
    assert resolve_agent_id("anything") == DEFAULT_AGENT_ID


def test_configured_keys_resolve_to_their_agent_id(monkeypatch):
    monkeypatch.setenv("QUORUM_AGENT_KEYS", '{"key-a": "agent-alpha", "key-b": "agent-beta"}')
    assert resolve_agent_id("key-a") == "agent-alpha"
    assert resolve_agent_id("key-b") == "agent-beta"


def test_missing_key_rejected_when_auth_configured(monkeypatch):
    monkeypatch.setenv("QUORUM_AGENT_KEYS", '{"key-a": "agent-alpha"}')
    with pytest.raises(InvalidAgentKey):
        resolve_agent_id(None)


def test_unrecognized_key_rejected_when_auth_configured(monkeypatch):
    monkeypatch.setenv("QUORUM_AGENT_KEYS", '{"key-a": "agent-alpha"}')
    with pytest.raises(InvalidAgentKey):
        resolve_agent_id("not-a-real-key")


def test_malformed_json_raises_value_error(monkeypatch):
    monkeypatch.setenv("QUORUM_AGENT_KEYS", "not json")
    with pytest.raises(ValueError):
        resolve_agent_id("key-a")


def test_non_object_json_raises_value_error(monkeypatch):
    monkeypatch.setenv("QUORUM_AGENT_KEYS", '["key-a"]')
    with pytest.raises(ValueError):
        resolve_agent_id("key-a")
