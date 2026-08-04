"""Title similarity for near-duplicate detection, plus an IDF-weighted cosine
metric for the emergent story-thread clusterer (``threads.py``).

The exact-match deduper (`dedupe.filter_new_items`) keys on
``item_id = sha256(source_name|link)``, so the *same story* republished by two
different outlets (e.g. Healthcare Dive and Becker's both covering "UnitedHealth,
FTC reach insulin settlement") is two distinct items and fires two alerts. This
module measures headline similarity so the alert path can suppress those
near-duplicate firings (see ``dedupe.suppress_duplicate_alerts``).

Similarity for that path is token-set Jaccard over content words — cheap,
order-insensitive, and good enough to catch reworded headlines about the same
event without a fuzzy-match dependency. ``jaccard`` / ``title_terms`` /
``title_similarity`` / ``is_near_duplicate`` are byte-identical to before this
module grew a second metric below — they are load-bearing for alert dedup and
must not change behavior.

``idf_weights`` / ``idf_norm`` / ``weighted_cosine`` are a *second*, unrelated
metric added for ``threads.py``'s clusterer. Plain Jaccard has no notion of how
common a term is *across the window* — in a Medicare Advantage headline corpus
"medicare" and "advantage" appear in well over half of all titles, so any two
unrelated MA headlines share a large, free chunk of Jaccard overlap before a
single discriminating word is counted. Weighting each term by its inverse
document frequency (rare terms count more, boilerplate counts near-zero) fixes
that without touching the dedup path at all: the two metrics are independent
and namespaced only by which caller uses them.
"""

import math
from collections import Counter

from ma_signal_monitor.keyword_mining import _tokens


def title_terms(title: str) -> set[str]:
    """Distinct content-word tokens of a title (stopwords/digits dropped)."""
    return set(_tokens(title))


def jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard overlap of two token sets: |A∩B| / |A∪B|. Empty/empty = 0.0."""
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


def title_similarity(a: str, b: str) -> float:
    """Similarity of two titles in [0, 1] via content-token Jaccard."""
    return jaccard(title_terms(a), title_terms(b))


def is_near_duplicate(a: str, b: str, threshold: float) -> bool:
    """True if two titles are near-duplicates at or above ``threshold``."""
    return title_similarity(a, b) >= threshold


def idf_weights(term_sets: list[set[str]]) -> dict[str, float]:
    """Inverse document frequency, ``log(n / df(t))``, for every term in a corpus.

    ``term_sets`` is one token *set* per document (presence, not frequency), so
    ``df(t)`` — how many documents contain ``t`` — is a simple sum. A term in
    every document (``df == n``) gets weight ``log(1) == 0.0``: maximally
    common terms contribute nothing to a weighted-cosine score, which is the
    whole point (see module docstring). Weights are meant to be computed once
    per corpus/window and reused across every pairwise comparison in it, never
    recomputed per pair. Empty corpus -> empty dict.
    """
    n = len(term_sets)
    if n == 0:
        return {}
    df: Counter = Counter()
    for ts in term_sets:
        df.update(ts)
    return {term: math.log(n / count) for term, count in df.items()}


def idf_norm(term_set: set[str], weights: dict[str, float]) -> float:
    """L2 norm of a token set's IDF-weight vector: ``sqrt(sum(w[t]**2))``.

    Pair this with ``weighted_cosine`` — compute each document's norm once
    per window (via this function) rather than once per pair, same as
    ``weights`` itself. A term absent from ``weights`` (shouldn't happen if
    ``weights`` came from a corpus containing ``term_set``, but guarded
    anyway) contributes 0.0. Empty set -> 0.0.
    """
    return math.sqrt(sum(weights.get(t, 0.0) ** 2 for t in term_set))


def weighted_cosine(
    a: set[str], b: set[str], weights: dict[str, float], norm_a: float, norm_b: float
) -> float:
    """IDF-weighted cosine similarity of two token sets, using precomputed norms.

    Each document is an implicit vector over the corpus vocabulary: term ``t``
    present -> coordinate ``weights[t]``, absent -> ``0``. Cosine similarity of
    two such (mostly-zero) vectors reduces to
    ``sum(weights[t]**2 for t in a & b) / (norm_a * norm_b)`` — only shared
    terms contribute, each squared by its own IDF weight, so ubiquitous terms
    (``weights[t]`` near 0) barely move the score while rare, discriminating
    terms dominate it. ``norm_a`` / ``norm_b`` are ``idf_norm(a, weights)`` /
    ``idf_norm(b, weights)``, passed in rather than recomputed here so a
    caller scoring many pairs against the same corpus computes each
    document's norm exactly once. Empty token set or all-zero-weight set
    (``norm == 0``) -> 0.0, matching ``jaccard``'s empty-set contract.
    """
    if not a or not b or norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    numerator = sum(weights.get(t, 0.0) ** 2 for t in a & b)
    return numerator / (norm_a * norm_b)
