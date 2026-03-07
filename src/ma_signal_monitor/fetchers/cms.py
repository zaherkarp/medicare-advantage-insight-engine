"""CMS public files fetcher - Phase 2 stub.

This module is structured for future implementation of CMS public data
file ingestion (e.g., plan landscape files, enrollment data).
The interface matches the RSS fetcher pattern.
"""

import logging

from ma_signal_monitor.config import SourceConfig
from ma_signal_monitor.models import RawFeedItem

logger = logging.getLogger("ma_signal_monitor.fetchers.cms")


def fetch_cms(
    source: SourceConfig,
    timeout: int = 30,
    user_agent: str = "MA-Signal-Monitor/1.0",
    max_items: int = 50,
) -> list[RawFeedItem]:
    """Fetch items from CMS public files. Currently a stub for Phase 2.

    Args:
        source: The source configuration.
        timeout: HTTP request timeout in seconds.
        user_agent: User-Agent header value.
        max_items: Maximum items to return.

    Returns:
        Empty list (stub implementation).
    """
    logger.info(
        "CMS fetcher is a Phase 2 stub. Source '%s' will be skipped.", source.name
    )
    return []
