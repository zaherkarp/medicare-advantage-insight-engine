"""RSS feed fetcher using feedparser."""

import logging
from html import unescape
from html.parser import HTMLParser

import feedparser
import requests

from ma_signal_monitor.config import SourceConfig
from ma_signal_monitor.models import RawFeedItem

logger = logging.getLogger("ma_signal_monitor.fetchers.rss")


class _HTMLTextExtractor(HTMLParser):
    """Minimal HTML tag stripper."""

    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts).strip()


def _strip_html(text: str) -> str:
    """Remove HTML tags from text, returning plain text."""
    if not text:
        return ""
    extractor = _HTMLTextExtractor()
    try:
        extractor.feed(unescape(text))
        return extractor.get_text()
    except Exception:
        return text


def fetch_rss(
    source: SourceConfig,
    timeout: int = 30,
    user_agent: str = "MA-Signal-Monitor/1.0",
    max_items: int = 50,
) -> list[RawFeedItem]:
    """Fetch items from an RSS feed source.

    Args:
        source: The source configuration.
        timeout: HTTP request timeout in seconds.
        user_agent: User-Agent header value.
        max_items: Maximum items to return (0 = unlimited).

    Returns:
        List of RawFeedItem objects.
    """
    logger.info("Fetching RSS: %s (%s)", source.name, source.url)
    items: list[RawFeedItem] = []

    try:
        response = requests.get(
            source.url,
            timeout=timeout,
            headers={"User-Agent": user_agent},
        )
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to fetch %s: %s", source.name, e)
        return items

    feed = feedparser.parse(response.content)

    if feed.bozo and not feed.entries:
        logger.warning(
            "Feed %s has parsing issues and no entries: %s",
            source.name,
            feed.bozo_exception,
        )
        return items

    entries = feed.entries
    if max_items > 0:
        entries = entries[:max_items]

    for entry in entries:
        title = entry.get("title", "").strip()
        if not title:
            continue

        link = entry.get("link", "").strip()
        published = entry.get("published", entry.get("updated", ""))

        # Get summary from various possible fields
        summary = ""
        if "summary" in entry:
            summary = entry.summary
        elif "description" in entry:
            summary = entry.description
        elif "content" in entry and entry.content:
            summary = entry.content[0].get("value", "")

        summary = _strip_html(summary)
        raw_content = (
            entry.get("content", [{}])[0].get("value", "")
            if entry.get("content")
            else ""
        )

        items.append(
            RawFeedItem(
                source_name=source.name,
                source_type=source.type,
                source_url=source.url,
                source_priority=source.priority,
                source_tags=source.tags,
                title=title,
                link=link,
                published=published,
                summary=summary,
                author=entry.get("author", ""),
                raw_content=_strip_html(raw_content),
            )
        )

    logger.info("Fetched %d items from %s", len(items), source.name)
    return items
