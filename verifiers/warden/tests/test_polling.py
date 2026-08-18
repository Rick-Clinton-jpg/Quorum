"""Tests for status extraction and file watcher helpers."""

from warden.polling.base import PollingStrategy
from warden.polling.file_watcher import FileWatcherStrategy


class Dummy(PollingStrategy):
    def poll(self, session_id: str):
        return None


def test_extract_most_recent():
    d = Dummy()
    text = """
Some noise
WARDEN_STATUS: first activity
more noise
WARDEN_STATUS: second activity is the one we want
"""
    assert d.extract_status(text) == "second activity is the one we want"


def test_extract_none():
    d = Dummy()
    assert d.extract_status("no status here") is None
    assert d.extract_status("") is None


def test_extract_with_whitespace():
    d = Dummy()
    text = "  WARDEN_STATUS:   cleaning up temp files  "
    assert d.extract_status(text) == "cleaning up temp files"


def test_file_watcher_missing(tmp_path):
    fw = FileWatcherStrategy(transcript_path=str(tmp_path / "missing.log"))
    assert fw.poll("any") is None