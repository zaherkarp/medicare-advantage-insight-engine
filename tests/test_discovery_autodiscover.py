"""Tests for feed autodiscovery (homepage <link rel> + path probing)."""

import responses

from ma_signal_monitor.discovery.autodiscover import discover_feeds

_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Insight Outlet</title>
  <item>
    <title>A Medicare Advantage story</title>
    <link>https://outlet.test/story/1</link>
    <description>Coverage of MA plans.</description>
  </item>
</channel></rss>
"""

_HTML_NO_ENTRIES = "<html><body><p>Not a feed</p></body></html>"


@responses.activate
def test_discover_via_link_rel():
    responses.add(
        responses.GET,
        "https://outlet.test/",
        body='<html><head><link rel="alternate" type="application/rss+xml" '
        'href="/feed.xml"></head><body></body></html>',
        status=200,
        content_type="text/html",
    )
    responses.add(
        responses.GET,
        "https://outlet.test/feed.xml",
        body=_RSS,
        status=200,
        content_type="application/rss+xml",
    )
    feeds = discover_feeds("outlet.test")
    assert len(feeds) == 1
    assert feeds[0].feed_url == "https://outlet.test/feed.xml"
    assert feeds[0].feed_title == "Insight Outlet"
    assert feeds[0].discovery_method == "link_rel"


@responses.activate
def test_discover_via_path_probe():
    # Homepage declares no feed; /feed is the first probed path and is valid.
    responses.add(
        responses.GET,
        "https://outlet.test/",
        body="<html><head></head><body>no feeds here</body></html>",
        status=200,
        content_type="text/html",
    )
    responses.add(
        responses.GET,
        "https://outlet.test/feed",
        body=_RSS,
        status=200,
        content_type="application/rss+xml",
    )
    feeds = discover_feeds("outlet.test")
    assert len(feeds) == 1
    assert feeds[0].feed_url == "https://outlet.test/feed"
    assert feeds[0].discovery_method == "path_probe"


@responses.activate
def test_rejects_non_feed_content():
    responses.add(
        responses.GET,
        "https://outlet.test/",
        body='<html><head><link rel="alternate" type="application/rss+xml" '
        'href="/feed.xml"></head></html>',
        status=200,
        content_type="text/html",
    )
    # The declared "feed" parses to zero entries → not a real feed.
    responses.add(
        responses.GET,
        "https://outlet.test/feed.xml",
        body=_HTML_NO_ENTRIES,
        status=200,
        content_type="text/html",
    )
    # No common paths registered either, so probing finds nothing.
    assert discover_feeds("outlet.test") == []


@responses.activate
def test_handles_unreachable_homepage():
    responses.add(responses.GET, "https://dead.test/", status=500)
    # Probe paths are unregistered → connection errors are swallowed.
    assert discover_feeds("dead.test") == []
