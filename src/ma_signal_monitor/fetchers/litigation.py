"""Litigation-tracker fetcher.

Consumes per-issue RSS feeds from health-care litigation trackers (e.g. the
Georgetown O'Neill Institute's Health Care Litigation Tracker,
``/issues/<slug>/feed/``). Those feeds carry the topic in the *feed* title
only — each entry's title is a case name and its summary is boilerplate
("The post … appeared first on …"). Because the source guarantees the topic
(every entry from ``/issues/star-ratings/feed/`` is a Medicare Advantage Star
Ratings case), this fetcher prepends ``source.context`` to each item's summary
so the scorer sees the real Medicare/MA context instead of scoring on the bare
case name.

Delegates the actual HTTP + parsing to the shared feed fetcher.
"""

import logging

from ma_signal_monitor.config import SourceConfig
from ma_signal_monitor.fetchers.rss import fetch_feed
from ma_signal_monitor.models import RawFeedItem

logger = logging.getLogger("ma_signal_monitor.fetchers.litigation")


def fetch_litigation(
    source: SourceConfig,
    timeout: int = 30,
    user_agent: str = "MA-Signal-Monitor/1.0",
    max_items: int = 50,
) -> list[RawFeedItem]:
    """Fetch cases from a litigation-tracker issue feed, injecting topic context.

    If ``source.context`` is set, it is prepended to each item's summary so the
    guaranteed topic (which the entries themselves omit) is visible to scoring.
    With no context configured, this behaves exactly like a plain RSS fetch.
    """
    items = fetch_feed(
        source, timeout=timeout, user_agent=user_agent, max_items=max_items
    )
    context = source.context.strip()
    if not context:
        return items
    for item in items:
        summary = item.summary.strip()
        item.summary = f"{context} {summary}".strip() if summary else context
    return items
