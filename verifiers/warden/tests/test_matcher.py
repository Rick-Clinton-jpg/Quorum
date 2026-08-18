"""Tests for keyword matcher."""

from warden.matcher import (
    compare_status_to_objective,
    compare_trigram,
    dual_compare,
    extract_keywords,
    similarity_score,
    trigram_score,
)


def test_extract_keywords_basic():
    kws = extract_keywords("Setting up FastAPI routes for score endpoints")
    # "FastAPI" now correctly splits into "fast" + "api" instead of staying fused
    assert "fast" in kws
    assert "api" in kws
    assert "route" in kws or "routes" in kws or any("rout" in k for k in kws)
    assert "the" not in kws
    assert "for" not in kws


def test_match_high_overlap():
    status = "Setting up FastAPI routes for /score and /debug endpoints"
    objective = "Build API wrapper for IntentGraph with /score and /debug endpoints"
    assert compare_status_to_objective(status, objective) in ("MATCH", "UNCLEAR")
    # Should be reasonably high
    assert similarity_score(status, objective) >= 0.15


def test_drift_clear():
    status = "Refactoring test suite for edge cases in scorer.py"
    objective = "Build API wrapper for IntentGraph with /score and /debug endpoints"
    tag = compare_status_to_objective(status, objective)
    # Likely DRIFT or UNCLEAR; should not be strong MATCH
    assert tag in ("DRIFT", "UNCLEAR")


def test_empty_status():
    assert compare_status_to_objective("", "Build something") == "UNCLEAR"
    assert compare_status_to_objective("   ", "Build something") == "UNCLEAR"


def test_identical():
    text = "Implement authentication middleware for the API"
    assert compare_status_to_objective(text, text) == "MATCH"
    assert similarity_score(text, text) == 1.0


def test_dual_compare_agreement_match():
    status = "Setting up FastAPI routes for /score and /debug endpoints"
    objective = "Build API wrapper for IntentGraph with /score and /debug endpoints"
    r = dual_compare(status, objective)
    assert r.word_tag == "MATCH"
    assert r.trigram_tag == "MATCH"
    assert r.final_tag == "MATCH"


def test_dual_compare_agreement_drift():
    status = "Refactoring test suite for edge cases in scorer.py"
    objective = "Build API wrapper for IntentGraph with /score and /debug endpoints"
    r = dual_compare(status, objective)
    assert r.final_tag == "DRIFT"


def test_dual_compare_divergent():
    # Word-overlap and trigram overlap disagree on this pair by construction —
    # this is the case the dual-rater design exists to catch.
    status = "Implementing the scoring and debugging endpoints"
    objective = "Build API wrapper for IntentGraph with /score and /debug endpoints"
    r = dual_compare(status, objective)
    assert r.word_tag != r.trigram_tag
    assert r.final_tag == "DIVERGENT"
    assert "disagree" in r.note.lower()


def test_dual_compare_identical():
    text = "Implement authentication middleware for the API"
    r = dual_compare(text, text)
    assert r.final_tag == "MATCH"
    assert r.word_score == 1.0
    assert r.trigram_score == 1.0