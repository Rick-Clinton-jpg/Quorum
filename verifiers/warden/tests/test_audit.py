"""Tests for NDJSON audit logger."""

import json
from pathlib import Path

from warden.audit import AuditLogger


def test_log_and_read(tmp_path: Path):
    logger = AuditLogger(root=tmp_path)
    path = logger.log(
        agent_id="test-session",
        objective="Build the thing",
        status="Writing routes",
        tag="MATCH",
        note="On track",
    )
    assert path.exists()
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["agent_id"] == "test-session"
    assert rec["tag"] == "MATCH"
    assert rec["action"] == "NONE"

    records = logger.read(session="test-session")
    assert len(records) == 1
    assert records[0]["status"] == "Writing routes"


def test_filter_by_tag(tmp_path: Path):
    logger = AuditLogger(root=tmp_path)
    logger.log(
        agent_id="s1",
        objective="o",
        status="a",
        tag="MATCH",
        note="n",
    )
    logger.log(
        agent_id="s1",
        objective="o",
        status="b",
        tag="DRIFT",
        note="n",
    )
    drifts = logger.read(tag="DRIFT")
    assert len(drifts) == 1
    assert drifts[0]["status"] == "b"