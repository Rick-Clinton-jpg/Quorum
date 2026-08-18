"""Keyword extraction and lightweight similarity matching (dual-rater)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Set

STOP_WORDS: Set[str] = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "are", "was", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "must", "shall", "can",
    "this", "that", "these", "those", "it", "its", "i", "you", "he",
    "she", "we", "they", "me", "him", "her", "us", "them", "my", "your",
    "his", "our", "their", "what", "which", "who", "whom", "whose",
    "where", "when", "why", "how", "all", "each", "every", "both",
    "few", "more", "most", "other", "some", "such", "no", "nor", "not",
    "only", "own", "same", "so", "than", "too", "very", "just", "about",
    "into", "over", "after", "before", "between", "under", "again",
    "further", "then", "once", "here", "there", "up", "down", "out",
    "off", "above", "below", "now", "also", "if", "any", "while",
}

_STEM_RULES = [
    (r"ing$", ""), (r"tion$", "t"), (r"sion$", "s"), (r"ness$", ""),
    (r"ment$", ""), (r"able$", ""), (r"ible$", ""), (r"ies$", "y"),
    (r"es$", ""), (r"s$", ""), (r"ed$", ""), (r"ly$", ""),
    (r"er$", ""), (r"est$", ""),
]

# Splits camelCase/PascalCase compounds BEFORE lowercasing so
# "FastAPI" -> "Fast" "API" -> tokens "fast", "api"
_COMPOUND_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

WORD_MATCH_THRESHOLD = 0.35
WORD_UNCLEAR_THRESHOLD = 0.15
TRIGRAM_MATCH_THRESHOLD = 0.30
TRIGRAM_UNCLEAR_THRESHOLD = 0.12


def _split_compound(word: str) -> list[str]:
    if not re.search(r"[a-z]", word) or not re.search(r"[A-Z]", word):
        return [word]
    return [p for p in _COMPOUND_SPLIT_RE.split(word) if p]


def _stem(word: str) -> str:
    if len(word) <= 3:
        return word
    for pattern, repl in _STEM_RULES:
        new = re.sub(pattern, repl, word)
        if new != word and len(new) >= 2:
            return new
    return word


def extract_keywords(text: str) -> list[str]:
    """Lowercase, split compounds (FastAPI -> fast+api), drop stop words, stem."""
    if not text:
        return []
    raw_tokens = re.findall(r"[A-Za-z0-9_]+", text)
    keywords = []
    for raw in raw_tokens:
        for piece in _split_compound(raw):
            t = piece.lower()
            if t in STOP_WORDS or len(t) < 2:
                continue
            keywords.append(_stem(t))
    return keywords


def similarity_score(status: str, objective: str) -> float:
    """Word-level Jaccard similarity (Rater A)."""
    s, o = set(extract_keywords(status)), set(extract_keywords(objective))
    if not s or not o:
        return 0.0
    return len(s & o) / len(s | o)


def compare_status_to_objective(status: str, objective: str) -> str:
    """Single-method (word-Jaccard) tag. Kept standalone for backward compatibility
    and as Rater A's independent verdict inside dual_compare."""
    if not status or not status.strip():
        return "UNCLEAR"
    if not extract_keywords(status) or not extract_keywords(objective):
        return "UNCLEAR"
    score = similarity_score(status, objective)
    if score >= WORD_MATCH_THRESHOLD:
        return "MATCH"
    if score >= WORD_UNCLEAR_THRESHOLD:
        return "UNCLEAR"
    return "DRIFT"


def _trigrams(text: str) -> Set[str]:
    text = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return set()
    padded = f"  {text}  "
    return {padded[i:i + 3] for i in range(len(padded) - 2)}


def trigram_score(status: str, objective: str) -> float:
    """Character-trigram Jaccard similarity (Rater B). Structurally different
    failure mode from word-Jaccard: catches morphological overlap stemming
    misses, and misses things word-overlap catches. That asymmetry is the point —
    it's what makes disagreement between the two raters an informative signal
    rather than noise."""
    a, b = _trigrams(status), _trigrams(objective)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def compare_trigram(status: str, objective: str) -> str:
    if not status or not status.strip():
        return "UNCLEAR"
    score = trigram_score(status, objective)
    if score >= TRIGRAM_MATCH_THRESHOLD:
        return "MATCH"
    if score >= TRIGRAM_UNCLEAR_THRESHOLD:
        return "UNCLEAR"
    return "DRIFT"


@dataclass
class DualResult:
    word_tag: str
    word_score: float
    trigram_tag: str
    trigram_score: float
    final_tag: str
    note: str


def dual_compare(status: str, objective: str) -> DualResult:
    """Run both raters independently. Agreement -> that tag, logged normally.
    Disagreement -> DIVERGENT. This is 'observe only' applied to the matcher
    itself: WARDEN doesn't adjudicate ambiguous cases, it surfaces them."""
    word_tag = compare_status_to_objective(status, objective)
    word_score = similarity_score(status, objective)
    tri_tag = compare_trigram(status, objective)
    tri_score = trigram_score(status, objective)

    if word_tag == tri_tag:
        final_tag = word_tag
        note = make_note(final_tag, status, objective, word_score)
    else:
        final_tag = "DIVERGENT"
        note = (
            f"Raters disagree — word-overlap says {word_tag} (score={word_score:.2f}), "
            f"trigram says {tri_tag} (score={tri_score:.2f}). Needs human review."
        )

    return DualResult(word_tag, word_score, tri_tag, tri_score, final_tag, note)


def make_note(tag: str, status: str, objective: str, score: float) -> str:
    if tag == "MATCH":
        return f"Status aligns with objective (score={score:.2f}). On track."
    if tag == "DRIFT":
        return (
            f"Objective: '{objective[:80]}…'. Status: '{status[:80]}…'. "
            f"Low overlap (score={score:.2f}). Possible drift — verify with user."
        )
    if not status:
        return "No WARDEN_STATUS found in last polling window."
    return f"Status unclear relative to objective (score={score:.2f})."
