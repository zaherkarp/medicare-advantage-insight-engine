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


def fetch_feed(
    source: SourceConfig,
    timeout: int = 30,
    user_agent: str = "MA-Signal-Monitor/1.0",
    max_items: int = 50,
) -> list[RawFeedItem]:
    """Fetch and parse any RSS/Atom feed into RawFeedItems.

    Shared by the RSS, SEC EDGAR, and CMS fetchers — they all consume Atom/RSS
    feeds and differ only in source configuration (URL, tags, priority).

    Args:
        source: The source configuration.
        timeout: HTTP request timeout in seconds.
        user_agent: User-Agent header value.
        max_items: Maximum items to return (0 = unlimited).

    Returns:
        List of RawFeedItem objects.

    Raises:
        requests.RequestException: on a network error or non-2xx response.
        Deliberately NOT swallowed here — the caller (main._fetch_one_source)
        both isolates it (one bad source can't stop the run) and records it
        as a distinct "error" outcome. Swallowing it into a bare empty return,
        as this used to do, made a source that's actually broken (e.g. a 403
        on every request) indistinguishable from one that's simply quiet —
        which is how 16 sources went unnoticed for months. See
        models.SourceFetchOutcome.
    """
    logger.info("Fetching feed: %s (%s)", source.name, source.url)
    items: list[RawFeedItem] = []

    response = requests.get(
        source.url,
        timeout=timeout,
        headers={"User-Agent": user_agent},
    )
    response.raise_for_status()

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
        summary_html = ""
        if "summary" in entry:
            summary_html = entry.summary
        elif "description" in entry:
            summary_html = entry.description
        elif "content" in entry and entry.content:
            summary_html = entry.content[0].get("value", "")

        raw_content = (
            entry.get("content", [{}])[0].get("value", "")
            if entry.get("content")
            else ""
        )

        # Preserve the un-stripped HTML before tags are removed, so source
        # discovery can harvest embedded <a href> links downstream.
        content_html = "\n".join(p for p in (summary_html, raw_content) if p)

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
                summary=_strip_html(summary_html),
                author=entry.get("author", ""),
                raw_content=_strip_html(raw_content),
                content_html=content_html,
            )
        )

    logger.info("Fetched %d items from %s", len(items), source.name)
    return items


def fetch_rss(
    source: SourceConfig,
    timeout: int = 30,
    user_agent: str = "MA-Signal-Monitor/1.0",
    max_items: int = 50,
) -> list[RawFeedItem]:
    """Fetch an RSS feed source (thin wrapper over fetch_feed)."""
    return fetch_feed(source, timeout, user_agent, max_items)
