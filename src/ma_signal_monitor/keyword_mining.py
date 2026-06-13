"""Mine keyword candidates from owner-labeled stories.

Owner verdicts (relevant / irrelevant) become labels; this surfaces the n-grams
most over-represented in each pool as **inclusion** candidates (frequent in
relevant stories, not already in the taxonomy) and **exclusion** candidates
(frequent in irrelevant stories). It ranks terms with the weighted log-odds
ratio with an informative Dirichlet prior (Monroe, Colaresi & Quinn 2008), whose
z-score controls for overall term frequency so rare terms don't dominate.

Output is advisory — a human reviews the candidates and edits ``taxonomy.yaml``.
Nothing here mutates config.
"""

import math
import re
from collections import Counter

from ma_signal_monitor.config import AppConfig
from ma_signal_monitor.storage import StateStore

# Small, domain-agnostic stopword list. Kept short on purpose — distinctive
# common words are handled by the log-odds prior, not by filtering.
_STOPWORDS = frozenset(
    """a an and are as at be by for from has have in is it its of on or that the
    to was were will with this these those they their has had not but our we you
    your his her them than then over under after before into out up down new
    more most said say says also can could would should may might one two""".split()
)
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'-]*[a-z0-9]|[a-z0-9]")


def _tokens(text: str) -> list[str]:
    """Lowercase word tokens, dropping stopwords and pure numbers."""
    out = []
    for tok in _TOKEN_RE.findall(text.lower()):
        if tok in _STOPWORDS or tok.isdigit():
            continue
        out.append(tok)
    return out


def _terms(text: str) -> set[str]:
    """Distinct uni- and bi-grams in a document (set: presence, not frequency)."""
    toks = _tokens(text)
    terms = set(toks)
    terms.update(f"{a} {b}" for a, b in zip(toks, toks[1:]))
    return terms


def _log_odds(
    pos_counts: Counter, neg_counts: Counter, vocab: set[str]
) -> dict[str, float]:
    """Weighted log-odds with an informative Dirichlet prior; returns z-scores.

    Positive z → over-represented in the positive pool, negative → in the
    negative pool. The background (combined) counts form the prior.
    """
    bg = {w: pos_counts[w] + neg_counts[w] for w in vocab}
    a0 = sum(bg.values())  # prior strength = total background mass
    n_pos = sum(pos_counts.values())
    n_neg = sum(neg_counts.values())
    z: dict[str, float] = {}
    for w in vocab:
        a_w = bg[w]  # prior count for this term
        y_pos = pos_counts[w] + a_w
        y_neg = neg_counts[w] + a_w
        # log-odds in each pool against the rest, then their difference
        delta = math.log(y_pos / (n_pos + a0 - y_pos)) - math.log(
            y_neg / (n_neg + a0 - y_neg)
        )
        var = 1.0 / y_pos + 1.0 / y_neg
        z[w] = delta / math.sqrt(var)
    return z


def mine_keywords(
    store: StateStore,
    config: AppConfig,
    *,
    min_docs: int = 3,
    top_n: int = 20,
) -> dict:
    """Rank inclusion/exclusion keyword candidates from labeled stories.

    Args:
        store: Open StateStore.
        config: App config (used to skip terms already in the taxonomy).
        min_docs: Ignore terms appearing in fewer than this many labeled docs.
        top_n: How many candidates to return per side.

    Returns:
        ``{"positives": int, "negatives": int, "inclusion": [...],
        "exclusion": [...]}``. Each candidate is
        ``{"term", "score", "relevant_docs", "irrelevant_docs"}``. When there
        are too few labels of either class, both lists are empty.
    """
    docs = store.get_labeled_documents()
    pos_docs = [t for t, v in docs if v == "relevant"]
    neg_docs = [t for t, v in docs if v == "irrelevant"]

    result = {"positives": len(pos_docs), "negatives": len(neg_docs)}
    if len(pos_docs) < min_docs or len(neg_docs) < min_docs:
        result["inclusion"] = []
        result["exclusion"] = []
        return result

    # Document-frequency counts (how many docs contain each term, per pool).
    pos_df: Counter = Counter()
    neg_df: Counter = Counter()
    for text in pos_docs:
        pos_df.update(_terms(text))
    for text in neg_docs:
        neg_df.update(_terms(text))

    vocab = {w for w in set(pos_df) | set(neg_df) if pos_df[w] + neg_df[w] >= min_docs}
    if not vocab:
        result["inclusion"] = []
        result["exclusion"] = []
        return result

    z = _log_odds(pos_df, neg_df, vocab)

    # Terms already covered by the taxonomy shouldn't be re-suggested for
    # inclusion (they're already in). Exclusion candidates are unconstrained.
    existing = {kw.lower() for cat in config.categories for kw in cat.keywords}

    def _entry(w: str) -> dict:
        return {
            "term": w,
            "score": round(z[w], 2),
            "relevant_docs": pos_df[w],
            "irrelevant_docs": neg_df[w],
        }

    ranked = sorted(vocab, key=lambda w: z[w], reverse=True)
    inclusion = [_entry(w) for w in ranked if z[w] > 0 and w not in existing][:top_n]
    exclusion = [_entry(w) for w in reversed(ranked) if z[w] < 0][:top_n]

    result["inclusion"] = inclusion
    result["exclusion"] = exclusion
    return result
