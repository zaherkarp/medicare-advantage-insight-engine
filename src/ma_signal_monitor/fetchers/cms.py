"""CMS / Medicaid.gov feed fetcher.

Consumes RSS/Atom feeds published by CMS and related agencies (newsroom,
data-update, and bulletin feeds) and normalizes them into RawFeedItems.
Delegates to the shared feed fetcher.
"""

import logging

from ma_signal_monitor.config import SourceConfig
from ma_signal_monitor.fetchers.rss import fetch_feed
from ma_signal_monitor.models import RawFeedItem

logger = logging.getLogger("ma_signal_monitor.fetchers.cms")


def fetch_cms(
    source: SourceConfig,
    timeout: int = 30,
    user_agent: str = "MA-Signal-Monitor/1.0",
    max_items: int = 50,
) -> list[RawFeedItem]:
    """Fetch items from a CMS RSS/Atom feed."""
    return fetch_feed(
        source, timeout=timeout, user_agent=user_agent, max_items=max_items
    )
