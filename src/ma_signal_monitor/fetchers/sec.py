"""SEC EDGAR fetcher - Phase 2 stub.

This module is structured for future implementation of SEC EDGAR
filing ingestion. The interface matches the RSS fetcher pattern
so it can be added to the source dispatch without changes elsewhere.
"""

import logging

from ma_signal_monitor.config import SourceConfig
from ma_signal_monitor.models import RawFeedItem

logger = logging.getLogger("ma_signal_monitor.fetchers.sec")


def fetch_sec(
    source: SourceConfig,
    timeout: int = 30,
    user_agent: str = "MA-Signal-Monitor/1.0",
    max_items: int = 50,
) -> list[RawFeedItem]:
    """Fetch items from SEC EDGAR. Currently a stub for Phase 2.

    Args:
        source: The source configuration.
        timeout: HTTP request timeout in seconds.
        user_agent: User-Agent header value.
        max_items: Maximum items to return.

    Returns:
        Empty list (stub implementation).
    """
    logger.info(
        "SEC fetcher is a Phase 2 stub. Source '%s' will be skipped.", source.name
    )
    return []
