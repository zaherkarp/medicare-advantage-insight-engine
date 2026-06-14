"""Surface stories where owner verdicts diverge from the relevance scorer.

The scorer assigns each story a relevance score; the owner's 👍/👎 verdicts are
ground truth. Where the two disagree is exactly where the scorer needs tuning:

- **over-scored** — the scorer cleared a story (score ≥ ``min_relevance_score``)
  that the owner marked *irrelevant*. False positives: candidates for a new
  exclusion keyword or a weight trim.
- **under-scored** — the scorer buried a story (score < threshold) that the
  owner marked *relevant* / *great*. False negatives: candidates for a new
  inclusion keyword or the golden set.

This module is pure (no I/O): it takes rows from
``StateStore.get_scored_owner_feedback`` and partitions them, ranking each side
by how far the score sits from the threshold so the worst mismatches surface
first. Output is advisory — a human decides what, if anything, to change.
``wrong_category`` verdicts are out of scope (they concern categorization, not
relevance) and are filtered upstream.
"""

# Verdicts that mean "the owner considers this relevant".
_POSITIVE = frozenset({"relevant", "great"})


def find_disagreements(rows: list[dict], threshold: float, *, top_n: int = 20) -> dict:
    """Partition scored, owner-labeled stories into scorer/reader disagreements.

    Args:
        rows: ``{"item_id", "title", "link", "source", "score", "verdict"}``
            dicts, one per story carrying its latest owner verdict.
        threshold: The scorer's relevance cutoff (``min_relevance_score``); a
            story at or above it is one the scorer treated as relevant.
        top_n: Max entries to return per side (worst disagreement first).

    Returns:
        ``{"labeled": int, "over_scored": [...], "under_scored": [...]}``. Each
        entry is the input row plus a ``gap`` — its distance from the threshold,
        so the most egregious mismatches sort to the top.
    """
    over: list[dict] = []
    under: list[dict] = []
    for r in rows:
        score = r["score"] if r["score"] is not None else 0.0
        verdict = r["verdict"]
        if verdict == "irrelevant" and score >= threshold:
            over.append({**r, "gap": round(score - threshold, 3)})
        elif verdict in _POSITIVE and score < threshold:
            under.append({**r, "gap": round(threshold - score, 3)})
    over.sort(key=lambda e: e["gap"], reverse=True)
    under.sort(key=lambda e: e["gap"], reverse=True)
    return {
        "labeled": len(rows),
        "over_scored": over[:top_n],
        "under_scored": under[:top_n],
    }
