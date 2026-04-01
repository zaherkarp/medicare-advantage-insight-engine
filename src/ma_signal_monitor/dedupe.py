"""Deduplication of normalized items against previously seen items."""

import logging

from ma_signal_monitor.models import NormalizedItem
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
