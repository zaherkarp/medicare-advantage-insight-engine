"""Harvest outbound links from ingested stories and aggregate them by domain.

Pure functions (no network or DB I/O) so they are trivially unit-testable. The
pipeline feeds in the stories it just scored; the resulting per-domain stats are
accumulated into ``candidate_domains`` for later feed autodiscovery. A domain's
score is the sum of the relevance scores of the stories that cite it, so a
domain repeatedly referenced by relevant coverage rises to the top.
"""

import logging
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

logger = logging.getLogger("ma_signal_monitor.discovery.harvest")

# Hosts that are never useful as feed sources (social, trackers, asset CDNs,
# link shorteners). Matched against the registrable domain and its subdomains.
_DENY_DOMAINS = {
    "twitter.com",
    "x.com",
    "facebook.com",
    "fb.com",
    "instagram.com",
    "linkedin.com",
    "youtube.com",
    "youtu.be",
    "tiktok.com",
    "reddit.com",
    "pinterest.com",
    "threads.net",
    "bsky.app",
    "t.co",
    "bit.ly",
    "ow.ly",
    "buff.ly",
    "lnkd.in",
    "googletagmanager.com",
    "google.com",
    "doubleclick.net",
    "gstatic.com",
    "googleapis.com",
    "cloudflare.com",
    "gravatar.com",
    "wp.com",
    "wordpress.com",
    "feedburner.com",
    "amazonaws.com",
    "akamaihd.net",
}

# URL schemes we never follow.
_BAD_SCHEMES = {"mailto", "tel", "javascript", "data", "ftp"}


class _LinkExtractor(HTMLParser):
    """Collect ``href`` values from ``<a>`` tags (mirrors rss._HTMLTextExtractor)."""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.hrefs.append(value.strip())


def extract_links(content_html: str, base_url: str = "") -> list[str]:
    """Return absolute http(s) links found in an article's HTML.

    Relative hrefs are resolved against ``base_url`` (the story's own link).
    Fragment-only links and bad schemes (mailto/tel/...) are dropped.
    """
    if not content_html:
        return []
    parser = _LinkExtractor()
    try:
        parser.feed(content_html)
    except Exception:  # malformed HTML — keep whatever was parsed so far
        pass

    links: list[str] = []
    for href in parser.hrefs:
        if not href or href.startswith("#"):
            continue
        if urlsplit(href).scheme.lower() in _BAD_SCHEMES:
            continue
        absolute = urljoin(base_url, href) if base_url else href
        if urlsplit(absolute).scheme.lower() not in ("http", "https"):
            continue
        links.append(absolute)
    return links


def domain_of(url: str) -> str:
    """Extract a normalized host (stdlib only).

    Lowercased hostname with a leading ``www.`` stripped. No ``tldextract``
    dependency, so compound TLDs (e.g. ``.co.uk``) keep their full host —
    acceptable for candidate ranking.
    """
    host = (urlsplit(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def is_denied(domain: str, exclude: set[str]) -> bool:
    """True if a domain should be ignored (denylist or already a source)."""
    if not domain or "." not in domain:
        return True
    if domain in exclude:
        return True
    # Match the denylist as a suffix so subdomains are caught too.
    return any(domain == bad or domain.endswith("." + bad) for bad in _DENY_DOMAINS)


@dataclass
class DomainStat:
    """Accumulated discovery signal for one outbound domain."""

    domain: str
    times_seen: int = 0
    relevance_score: float = 0.0
    example_link: str = ""
    example_story_id: str = ""


@dataclass
class StoryLinks:
    """The discovery-relevant slice of a scored story."""

    item_id: str
    link: str  # the story's own URL (also used to resolve relative hrefs)
    content_html: str
    relevance_score: float


def harvest_domains(
    stories,
    *,
    min_score: float = 0.0,
    exclude_domains: set[str] | None = None,
) -> dict[str, DomainStat]:
    """Aggregate outbound domains across stories, weighted by relevance.

    Only stories scoring at/above ``min_score`` contribute, and each domain is
    counted at most once per story. The story's own domain and any domain in
    ``exclude_domains`` (typically the already-configured sources) are skipped.
    """
    exclude = exclude_domains or set()
    stats: dict[str, DomainStat] = {}
    for story in stories:
        if story.relevance_score < min_score:
            continue
        own = domain_of(story.link)
        urls = extract_links(story.content_html, story.link)
        seen_here: set[str] = set()
        for url in urls:
            dom = domain_of(url)
            if dom == own or dom in seen_here or is_denied(dom, exclude):
                continue
            seen_here.add(dom)
            stat = stats.get(dom)
            if stat is None:
                stat = DomainStat(
                    domain=dom,
                    example_link=url,
                    example_story_id=story.item_id,
                )
                stats[dom] = stat
            stat.times_seen += 1
            stat.relevance_score += story.relevance_score
    return stats


def configured_source_domains(sources) -> set[str]:
    """Domains already covered by configured sources (feed url + homepage)."""
    out: set[str] = set()
    for s in sources:
        for u in (getattr(s, "url", ""), getattr(s, "homepage", "")):
            d = domain_of(u)
            if d:
                out.add(d)
    return out
