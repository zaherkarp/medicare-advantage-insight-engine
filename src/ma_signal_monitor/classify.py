"""Classification of scored items into trigger categories.

This module assigns the primary trigger category from the taxonomy
to each scored item. The category is already partially determined
during scoring; this module selects the primary one and provides
a human-readable classification label.
"""

import logging

from ma_signal_monitor.config import AppConfig
from ma_signal_monitor.models import ScoredItem

logger = logging.getLogger("ma_signal_monitor.classify")


def classify_item(scored: ScoredItem, config: AppConfig) -> str:
    """Determine the primary trigger category for a scored item.

    If the item matched multiple categories during scoring, this selects
    the one with the highest taxonomy weight. If no categories matched,
    returns "uncategorized".

    Args:
        scored: The scored item.
        config: Application configuration.

    Returns:
        The key of the primary trigger category.
    """
    if not scored.matched_categories:
        return "uncategorized"

    if len(scored.matched_categories) == 1:
        return scored.matched_categories[0]

    # Pick the category with highest weight
    cat_weights = {
        cat.key: cat.weight for cat in config.categories
    }
    best = max(
        scored.matched_categories,
        key=lambda k: cat_weights.get(k, 0.0),
    )
    return best


def get_category_label(category_key: str, config: AppConfig) -> str:
    """Get the human-readable label for a category key.

    Args:
        category_key: The internal category key.
        config: Application configuration.

    Returns:
        Human-readable label, or the key itself if not found.
    """
    for cat in config.categories:
        if cat.key == category_key:
            return cat.label
    return category_key
