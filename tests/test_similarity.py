"""Tests for title-similarity (near-duplicate detection) and IDF-weighted
cosine (the emergent story-thread clusterer's metric)."""

import math

from ma_signal_monitor.similarity import (
    idf_norm,
    idf_weights,
    is_near_duplicate,
    jaccard,
    title_similarity,
    title_terms,
    weighted_cosine,
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


# --- Anti-regression: jaccard / title_terms / title_similarity /
# is_near_duplicate are byte-identical to before this module grew
# idf_weights/idf_norm/weighted_cosine below. They are the alert-dedup path
# (dedupe.suppress_duplicate_alerts) -- pinning exact values, not just the
# inequalities the tests above already check, catches any future change to
# this metric immediately, independent of the new functions' own tests.


def test_title_similarity_exact_value_is_pinned():
    a = "UnitedHealth, FTC reach insulin settlement"
    b = "UnitedHealth and FTC reach a proposed insulin settlement"
    assert title_similarity(a, b) == jaccard(title_terms(a), title_terms(b))
    assert (
        title_similarity(a, b) == 5 / 6
    )  # {ftc,insulin,settlement,unitedhealth,reach} / 6-term union
    c = "CMS finalizes 2027 Star Ratings methodology"
    assert title_similarity(a, c) == 0.0  # no shared content tokens at all


# --- idf_weights / idf_norm / weighted_cosine (threads.py's clusterer metric) ---


def test_idf_weights_empty_corpus_is_empty_dict():
    assert idf_weights([]) == {}


def test_idf_weights_is_log_n_over_df():
    # "a" appears in every doc (df=3=n) -> weight log(1) == 0.0 (fully
    # non-discriminating). "b" appears in one of three -> log(3).
    corpus = [{"a", "b"}, {"a", "c"}, {"a", "d"}]
    weights = idf_weights(corpus)
    assert weights["a"] == 0.0
    assert weights["b"] == math.log(3 / 1)
    assert weights["c"] == math.log(3 / 1)


def test_idf_norm_matches_manual_l2_norm():
    weights = {"x": 2.0, "y": 3.0, "z": 5.0}
    assert idf_norm({"x", "y"}, weights) == math.sqrt(2.0**2 + 3.0**2)
    assert idf_norm(set(), weights) == 0.0
    # A term absent from the weight map contributes 0, not a KeyError.
    assert idf_norm({"unknown"}, weights) == 0.0


def test_weighted_cosine_only_shared_terms_contribute():
    weights = {"shared1": 1.0, "shared2": 2.0, "onlya": 5.0, "onlyb": 7.0}
    a, b = {"shared1", "shared2", "onlya"}, {"shared1", "shared2", "onlyb"}
    norm_a = idf_norm(a, weights)
    norm_b = idf_norm(b, weights)
    expected = (1.0**2 + 2.0**2) / (norm_a * norm_b)
    assert weighted_cosine(a, b, weights, norm_a, norm_b) == expected


def test_weighted_cosine_disjoint_sets_score_zero():
    weights = {"p": 1.0, "q": 1.0}
    assert weighted_cosine({"p"}, {"q"}, weights, 1.0, 1.0) == 0.0


def test_weighted_cosine_empty_set_never_matches():
    # Mirrors jaccard's empty/empty and empty/nonempty contract (both 0.0).
    weights = {"p": 1.0}
    assert weighted_cosine(set(), set(), weights, 0.0, 0.0) == 0.0
    assert weighted_cosine(set(), {"p"}, weights, 0.0, 1.0) == 0.0


def test_weighted_cosine_zero_norm_guards_divide_by_zero():
    # A nonempty set whose only terms are window-ubiquitous (weight 0) has
    # norm 0.0 -- must not raise ZeroDivisionError.
    weights = {"ubiquitous": 0.0}
    assert weighted_cosine({"ubiquitous"}, {"ubiquitous"}, weights, 0.0, 0.0) == 0.0


def test_weighted_cosine_identical_sets_score_one():
    weights = {"a": 1.5, "b": 2.5}
    s = {"a", "b"}
    norm = idf_norm(s, weights)
    assert weighted_cosine(s, s, weights, norm, norm) == 1.0
