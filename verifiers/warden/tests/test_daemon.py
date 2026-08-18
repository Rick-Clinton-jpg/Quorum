"""Tests for daemon scheduling logic."""

from datetime import datetime, timedelta
from pathlib import Path

from warden.core import Session, Warden


def test_is_due_never_checked(tmp_path: Path):
    w = Warden(root=tmp_path / ".warden")
    s = Session(agent_id="x", agent_type="manual", objective="obj")
    assert w._is_due(s, datetime.now().astimezone()) is True


def test_is_due_respects_interval(tmp_path: Path):
    w = Warden(root=tmp_path / ".warden")
    now = datetime.now().astimezone()
    s = Session(
        agent_id="x", agent_type="manual", objective="obj",
        interval_seconds=600,
        last_checked=(now - timedelta(seconds=30)).isoformat(timespec="seconds"),
    )
    assert w._is_due(s, now) is False

    s.last_checked = (now - timedelta(seconds=700)).isoformat(timespec="seconds")
    assert w._is_due(s, now) is True


def test_is_due_independent_per_session_interval(tmp_path: Path):
    """Sessions with different --interval values are due on their own
    schedule, not in lockstep with each other."""
    w = Warden(root=tmp_path / ".warden")
    now = datetime.now().astimezone()
    checked_20s_ago = (now - timedelta(seconds=20)).isoformat(timespec="seconds")

    fast = Session(
        agent_id="fast", agent_type="manual", objective="obj",
        interval_seconds=10, last_checked=checked_20s_ago,
    )
    slow = Session(
        agent_id="slow", agent_type="manual", objective="obj",
        interval_seconds=60, last_checked=checked_20s_ago,
    )

    assert w._is_due(fast, now) is True
    assert w._is_due(slow, now) is False
