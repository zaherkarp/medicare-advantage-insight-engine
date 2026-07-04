"""Deduplication of normalized items against previously seen items."""

import logging

from ma_signal_monitor.config import AppConfig
from ma_signal_monitor.models import Alert, NormalizedItem, ScoredItem
from ma_signal_monitor.similarity import jaccard, title_terms
from ma_signal_monitor.storage import StateStore

logger = logging.getLogger("ma_signal_monitor.dedupe")


def filter_new_items(
    items: list[NormalizedItem], store: StateStore
) -> list[NormalizedItem]:
    """Filter out items that have already been seen.

    Args:
        items: List of normalized items to check.
        store: State store for dedup lookups.

    Returns:
        List of items not previously seen.
    """
    new_items = []
    duplicate_count = 0

    for item in items:
        if store.is_seen(item.item_id):
            duplicate_count += 1
            logger.debug("Duplicate skipped: %s", item.title[:80])
        else:
            new_items.append(item)

    logger.info(
        "Dedup: %d new items, %d duplicates filtered from %d total",
        len(new_items),
        duplicate_count,
        len(items),
    )
    return new_items


def suppress_duplicate_alerts(
    alerts: list[Alert], store: StateStore, config: AppConfig
) -> tuple[list[Alert], int]:
    """Drop near-duplicate alerts so the same story fires only one webhook.

    Two independent passes, both keyed on headline similarity (title-token
    Jaccard, see ``similarity``):

    1. **Within-run** — the same story republished by two sources in one run is
       two distinct items (different ``item_id``) that both cleared the alert
       threshold. Walking the alerts in their existing order (score-descending,
       since ``score_items`` sorts and ``draft_alerts`` preserves order), keep
       the first of each near-duplicate cluster and drop the rest — so the
       kept representative is the highest-scoring one.
    2. **Cross-run** — drop any survivor whose title near-matches an alert
       already delivered in the last ``dedup_lookback_days``.

    Only the webhook stream is trimmed; ``_persist_stories`` still archives
    every scored item. Returns ``(kept_alerts, suppressed_count)``. A no-op
    (returns the input unchanged) when ``dedup_enabled`` is false.
    """
    if not config.dedup_enabled or not alerts:
        return alerts, 0

    threshold = config.dedup_similarity_threshold

    # Cross-run reference: term sets of recently-alerted titles.
    recent_terms = [
        terms
        for title in store.recent_alert_titles(config.dedup_lookback_days)
        if (terms := title_terms(title))
    ]

    kept: list[Alert] = []
    kept_terms: list[set[str]] = []
    suppressed = 0
    for alert in alerts:
        title = alert.internal.title
        terms = title_terms(title)
        # Within-run: near-duplicate of an already-kept alert?
        if any(jaccard(terms, prev) >= threshold for prev in kept_terms):
            suppressed += 1
            logger.debug("Suppressed within-run duplicate alert: %s", title[:80])
            continue
        # Cross-run: near-duplicate of a recently-delivered alert?
        if any(jaccard(terms, prev) >= threshold for prev in recent_terms):
            suppressed += 1
            logger.debug("Suppressed cross-run duplicate alert: %s", title[:80])
            continue
        kept.append(alert)
        kept_terms.append(terms)

    if suppressed:
        logger.info(
            "Alert dedup: %d kept, %d near-duplicates suppressed", len(kept), suppressed
        )
    return kept, suppressed


def assign_story_duplicates(
    scored_items: list[ScoredItem], store: StateStore, config: AppConfig
) -> dict[str, str | None]:
    """Map each scored item to the representative story it near-duplicates.

    Mirrors :func:`suppress_duplicate_alerts` but for the *archive*: it labels
    (rather than drops) near-duplicates so the browsable feed can show one
    representative per story while the archive keeps every row. Two passes on
    headline similarity:

    1. **Within-run** — walking items in score-descending order (``score_items``
       sorts), the first of a near-duplicate cluster is the representative and
       the rest point at its ``item_id``.
    2. **Cross-run** — an item that near-matches a representative archived in the
       last ``story_dedup_lookback_days`` points at that archived representative
       (``recent_story_reps`` returns only roots, so chains never form).

    Returns ``{item_id: representative_item_id_or_None}``. Every item maps to
    None (all representatives) when ``story_dedup_enabled`` is false.
    """
    result: dict[str, str | None] = {s.item.item_id: None for s in scored_items}
    if not config.story_dedup_enabled or not scored_items:
        return result

    threshold = config.dedup_similarity_threshold
    recent_reps = [
        (item_id, terms)
        for item_id, title in store.recent_story_reps(config.story_dedup_lookback_days)
        if (terms := title_terms(title))
    ]

    kept: list[tuple[str, set[str]]] = []  # (representative item_id, terms)
    marked = 0
    for s in scored_items:
        item_id = s.item.item_id
        terms = title_terms(s.item.title)
        rep = next(
            (rid for rid, prev in kept if jaccard(terms, prev) >= threshold),
            None,
        ) or next(
            (rid for rid, prev in recent_reps if jaccard(terms, prev) >= threshold),
            None,
        )
        if rep is not None:
            result[item_id] = rep
            marked += 1
            logger.debug("Story marked duplicate of %s: %s", rep, s.item.title[:80])
        else:
            kept.append((item_id, terms))

    if marked:
        logger.info("Story dedup: %d near-duplicates marked in the archive", marked)
    return result


def mark_items_seen(items: list[NormalizedItem], store: StateStore) -> None:
    """Mark items as seen in the state store.

    Call this after items have been processed (scored, delivered, etc.)
    so they won't be processed again on the next run.

    Args:
        items: Items to mark as seen.
        store: State store instance.
    """
    for item in items:
        store.mark_seen(
            item_id=item.item_id,
            source_name=item.source_name,
            title=item.title,
            link=item.link,
        )
    logger.debug("Marked %d items as seen", len(items))
