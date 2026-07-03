"""Tests for title-similarity (near-duplicate detection)."""

from ma_signal_monitor.similarity import (
    is_near_duplicate,
    jaccard,
    title_similarity,
    title_terms,
)


def test_title_terms_drops_stopwords_and_case():
    terms = title_terms("The UnitedHealth and FTC Settlement")
    assert "unitedhealth" in terms
    assert "settlement" in terms
    assert "the" not in terms  # stopword
    assert "and" not in terms


def test_jaccard_bounds():
    assert jaccard(set(), set()) == 0.0
    assert jaccard({"a"}, set()) == 0.0
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert jaccard({"a", "b"}, {"b", "c"}) == 1 / 3  # 1 shared / 3 union


def test_near_duplicate_headlines_score_high():
    a = "UnitedHealth, FTC reach insulin settlement"
    b = "UnitedHealth and FTC reach a proposed insulin settlement"
    assert title_similarity(a, b) >= 0.6
    assert is_near_duplicate(a, b, 0.6)


def test_distinct_headlines_score_low():
    a = "UnitedHealth, FTC reach insulin settlement"
    b = "CMS finalizes 2027 Star Ratings methodology"
    assert title_similarity(a, b) < 0.3
    assert not is_near_duplicate(a, b, 0.6)


def test_threshold_is_inclusive():
    # Identical titles → 1.0, near-dup at exactly the threshold still matches.
    assert is_near_duplicate("Humana cuts guidance", "Humana cuts guidance", 1.0)


def test_empty_titles_never_match():
    # An empty/stopword-only title shares no content tokens, so it is never a
    # near-duplicate of a real headline at any positive threshold.
    assert title_similarity("", "") == 0.0
    assert not is_near_duplicate("", "anything at all", 0.6)
    assert not is_near_duplicate("the and of", "Humana cuts guidance", 0.6)
