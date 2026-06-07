"""SEC EDGAR fetcher.

Consumes SEC EDGAR Atom feeds (e.g. a company's recent filings via
``browse-edgar?...&output=atom``) and normalizes them into RawFeedItems. EDGAR
serves standard Atom, so this delegates to the shared feed fetcher.

Note: the SEC requires a descriptive User-Agent (set via the USER_AGENT env /
config) and rate-limits aggressive clients — keep ingestion intervals modest.
"""

import logging

from ma_signal_monitor.config import SourceConfig
from ma_signal_monitor.fetchers.rss import fetch_feed
from ma_signal_monitor.models import RawFeedItem

logger = logging.getLogger("ma_signal_monitor.fetchers.sec")


def fetch_sec(
    source: SourceConfig,
    timeout: int = 30,
    user_agent: str = "MA-Signal-Monitor/1.0",
    max_items: int = 50,
) -> list[RawFeedItem]:
    """Fetch filings from a SEC EDGAR Atom feed."""
    return fetch_feed(
        source, timeout=timeout, user_agent=user_agent, max_items=max_items
    )
