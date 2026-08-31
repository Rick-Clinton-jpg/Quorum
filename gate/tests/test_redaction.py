"""Tests for gate/redaction.py's scrub_pii().

The critical case isn't just "does it catch PII" - it's "does it leave
legitimate, non-PII content (commit hashes, trace IDs, UUIDs) alone."
An earlier, rejected draft of this fix used an entropy/long-random-
string heuristic that would have destroyed exactly that content.
"""

from __future__ import annotations

from gate.redaction import scrub_pii


def test_redacts_a_formatted_email():
    assert "jane.doe@example.com" not in scrub_pii("Contact jane.doe@example.com please")


def test_redacts_a_spelled_out_email():
    assert "example" not in scrub_pii("reach out at jane dot doe at example dot com")


def test_redacts_an_ssn():
    assert "123-45-6789" not in scrub_pii("SSN on file: 123-45-6789")


def test_redacts_a_credit_card_formatted_number():
    assert "4111 1111 1111 1111" not in scrub_pii("card: 4111 1111 1111 1111")


def test_leaves_a_git_commit_sha_untouched():
    text = "commit 8f3a2c9b1e7d4560abf12345d6e7890fabc1234de"
    assert scrub_pii(text) == text


def test_leaves_a_uuid_untouched():
    text = "trace_id=550e8400-e29b-41d4-a716-446655440000"
    assert scrub_pii(text) == text


def test_leaves_ordinary_text_untouched():
    text = "This change follows the same review process as every other change."
    assert scrub_pii(text) == text


def test_handles_none_and_empty_string():
    assert scrub_pii("") == ""
    assert scrub_pii(None) is None
