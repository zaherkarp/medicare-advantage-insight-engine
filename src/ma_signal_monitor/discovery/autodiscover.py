"""Autodiscover RSS/Atom feeds on a candidate domain.

Fetches the domain's homepage, looks for ``<link rel="alternate">`` feed
declarations, then falls back to probing common feed paths. Each candidate is
validated by actually parsing it with ``feedparser`` (the same library the
fetchers use), so only URLs that yield real feed entries are returned. Mirrors
the ``requests`` + ``feedparser`` pattern in ``fetchers/rss.fetch_feed``.
"""

import logging
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin

import feedparser
import requests

logger = logging.getLogger("ma_signal_monitor.discovery.autodiscover")

# <link type="..."> values that indicate a feed.
_FEED_TYPES = {
    "application/rss+xml",
    "application/atom+xml",
    "application/xml",
    "text/xml",
}

# Probed in order when no <link rel=alternate> feed is declared.
_COMMON_PATHS = (
    "/feed",
    "/feed/",
    "/rss",
    "/rss.xml",
    "/atom.xml",
    "/index.xml",
    "/feeds/posts/default",
)


@dataclass
class FeedCandidate:
    """A validated feed discovered on a domain."""

    feed_url: str
    feed_title: str
    discovery_method: str  # "link_rel" | "path_probe"


class _FeedLinkExtractor(HTMLParser):
    """Collect ``<link rel="alternate" type="...rss/atom...">`` hrefs."""

    def __init__(self) -> None:
        super().__init__()
        self.feeds: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        href = a.get("href", "").strip()
        if (
            href
            and "alternate" in a.get("rel", "").lower()
            and a.get("type", "").lower() in _FEED_TYPES
        ):
            self.feeds.append(href)


def _http_get(url: str, timeout: int, user_agent: str) -> requests.Response:
    return requests.get(url, timeout=timeout, headers={"User-Agent": user_agent})


def _validate_feed(url: str, timeout: int, user_agent: str) -> tuple[bool, str]:
    """Return (is_feed, title). Valid only if the URL parses to >= 1 entry."""
    try:
        resp = _http_get(url, timeout, user_agent)
        resp.raise_for_status()
    except requests.RequestException:
        return False, ""
    parsed = feedparser.parse(resp.content)
    if not parsed.entries:
        return False, ""
    return True, (parsed.feed.get("title") or "").strip()


def discover_feeds(
    domain: str,
    *,
    timeout: int = 30,
    user_agent: str = "MA-Signal-Monitor/1.0",
) -> list[FeedCandidate]:
    """Discover validated feeds for a bare domain (e.g. ``"example.com"``)."""
    homepage = f"https://{domain}/"
    candidates: list[FeedCandidate] = []
    seen: set[str] = set()

    # 1. Parse the homepage for declared feeds.
    declared: list[str] = []
    try:
        resp = _http_get(homepage, timeout, user_agent)
        resp.raise_for_status()
        extractor = _FeedLinkExtractor()
        extractor.feed(resp.text)
        declared = [urljoin(homepage, h) for h in extractor.feeds]
    except requests.RequestException as e:
        logger.debug("Homepage fetch failed for %s: %s", domain, e)
    except Exception as e:  # malformed HTML
        logger.debug("Homepage parse failed for %s: %s", domain, e)

    for url in declared:
        if url in seen:
            continue
        seen.add(url)
        ok, title = _validate_feed(url, timeout, user_agent)
        if ok:
            candidates.append(FeedCandidate(url, title, "link_rel"))

    # 2. If nothing was declared/validated, probe common paths.
    if not candidates:
        for path in _COMMON_PATHS:
            url = urljoin(homepage, path)
            if url in seen:
                continue
            seen.add(url)
            ok, title = _validate_feed(url, timeout, user_agent)
            if ok:
                candidates.append(FeedCandidate(url, title, "path_probe"))
                break  # one good feed per domain is enough

    logger.info("Discovered %d feed(s) for %s", len(candidates), domain)
    return candidates
