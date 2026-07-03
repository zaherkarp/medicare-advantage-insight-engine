"""Normalization of raw feed items into a standard schema."""

import hashlib
import logging
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser

from ma_signal_monitor.models import NormalizedItem, RawFeedItem

logger = logging.getLogger("ma_signal_monitor.normalize")


class _HTMLStripper(HTMLParser):
    """Collects text content, discarding tags."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def text(self) -> str:
        return " ".join(self._parts)


def strip_html(text: str) -> str:
    """Strip HTML tags/entities and collapse whitespace.

    Some feeds (e.g. Fierce Healthcare) wrap titles in an ``<a>`` tag; without
    this the markup renders as literal text in the UI.
    """
    if not text:
        return ""
    stripper = _HTMLStripper()
    try:
        stripper.feed(unescape(text))
        out = stripper.text()
    except Exception:
        out = text
    return re.sub(r"\s+", " ", out).strip()


# Common date formats found in RSS feeds
_DATE_FORMATS = [
    "%a, %d %b %Y %H:%M:%S %z",
    "%a, %d %b %Y %H:%M:%S %Z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
]


def _parse_date(date_str: str) -> datetime | None:
    """Parse a date string from various feed formats.

    Tries RFC 2822 first (email.utils), then common formats.
    Returns None if parsing fails.
    """
    if not date_str:
        return None

    # Try RFC 2822 (most common in RSS)
    try:
        return parsedate_to_datetime(date_str)
    except (ValueError, TypeError):
        pass

    # Try common formats
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue

    logger.debug("Could not parse date: %s", date_str)
    return None


def _generate_item_id(item: RawFeedItem) -> str:
    """Generate a stable, unique identifier for deduplication.

    Uses a hash of source + link + title to handle feeds that don't
    provide stable GUIDs. Link alone is preferred if available.
    """
    if item.link:
        key = f"{item.source_name}|{item.link}"
    else:
        key = f"{item.source_name}|{item.title}"

    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _truncate(text: str, max_length: int) -> str:
    """Truncate text to max_length, adding ellipsis if truncated."""
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


def _clean_text(text: str) -> str:
    """Clean whitespace and normalize text."""
    if not text:
        return ""
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_item(raw: RawFeedItem, max_summary_length: int = 500) -> NormalizedItem:
    """Normalize a single raw feed item.

    Args:
        raw: The raw feed item.
        max_summary_length: Maximum characters for the summary.

    Returns:
        A NormalizedItem with cleaned, standardized fields.
    """
    return NormalizedItem(
        item_id=_generate_item_id(raw),
        source_name=raw.source_name,
        source_type=raw.source_type,
        source_priority=raw.source_priority,
        source_tags=raw.source_tags,
        title=strip_html(raw.title),
        link=raw.link.strip(),
        published_date=_parse_date(raw.published),
        summary=_truncate(_clean_text(raw.summary), max_summary_length),
        author=_clean_text(raw.author),
    )


def normalize_items(
    raw_items: list[RawFeedItem], max_summary_length: int = 500
) -> list[NormalizedItem]:
    """Normalize a list of raw feed items.

    Args:
        raw_items: List of raw feed items.
        max_summary_length: Maximum characters for each summary.

    Returns:
        List of NormalizedItem objects.
    """
    normalized = []
    blank = 0
    for raw in raw_items:
        try:
            item = normalize_item(raw, max_summary_length)
        except Exception as e:
            logger.warning(
                "Failed to normalize item '%s' from %s: %s",
                raw.title[:50],
                raw.source_name,
                e,
            )
            continue
        # A malformed feed can emit entries with no readable text at all
        # (observed in production: 30 title-less, summary-less stories from
        # one broken feed). There is nothing to score, display, or alert on —
        # drop them here instead of polluting the archive.
        if not item.title and not item.summary:
            blank += 1
            continue
        normalized.append(item)
    if blank:
        logger.warning("Dropped %d blank items (no title or summary)", blank)
    logger.info("Normalized %d / %d items", len(normalized), len(raw_items))
    return normalized
