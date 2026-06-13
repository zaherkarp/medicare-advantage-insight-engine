"""Source-yield review policy.

Formalizes the manual "this source never produces anything relevant, disable it"
decision into a flag-for-review rule. This module is pure (no I/O): it takes the
per-source yield stats from ``StateStore.get_source_yield`` and decides which
sources have earned a second look — it never disables anything itself. A human
confirms the disable (e.g. by editing ``sources.yaml``), matching the project's
"false positives over false negatives" stance.
"""

from ma_signal_monitor.config import AppConfig


def flag_low_yield_sources(stats: list[dict], config: AppConfig) -> list[dict]:
    """Return the subset of ``stats`` that should be flagged for review.

    A source is flagged once it has a fair sample and still under-delivers:
    enough items ingested (``source_review_min_sample``), a relevance yield below
    ``source_review_yield_floor``, AND a best-ever score below
    ``source_review_max_score_floor`` (so a source with occasional strong hits is
    spared). Each flagged entry carries a human-readable ``reason``.
    """
    flagged = []
    for s in stats:
        if s["total"] < config.source_review_min_sample:
            continue
        if (
            s["yield"] < config.source_review_yield_floor
            and s["max_score"] < config.source_review_max_score_floor
        ):
            reason = (
                f"{s['relevant']}/{s['total']} relevant "
                f"({s['yield'] * 100:.0f}%), max score {s['max_score']:.2f}"
            )
            flagged.append({**s, "reason": reason})
    return flagged
