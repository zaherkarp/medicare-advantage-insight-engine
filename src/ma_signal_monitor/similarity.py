"""Title similarity for near-duplicate detection.

The exact-match deduper (`dedupe.filter_new_items`) keys on
``item_id = sha256(source_name|link)``, so the *same story* republished by two
different outlets (e.g. Healthcare Dive and Becker's both covering "UnitedHealth,
FTC reach insulin settlement") is two distinct items and fires two alerts. This
module measures headline similarity so the alert path can suppress those
near-duplicate firings (see ``dedupe.suppress_duplicate_alerts``).

Similarity is token-set Jaccard over content words — cheap, order-insensitive,
and good enough to catch reworded headlines about the same event without a
fuzzy-match dependency.
"""

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
