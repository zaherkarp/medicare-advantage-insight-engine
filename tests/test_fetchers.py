"""Tests for the SEC and CMS feed fetchers (delegating to the shared core)."""

import pytest
import requests
import responses

from ma_signal_monitor.config import SourceConfig
from ma_signal_monitor.fetchers.cms import fetch_cms
from ma_signal_monitor.fetchers.sec import fetch_sec

_SEC_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>UnitedHealth Group - Filings</title>
  <entry>
    <title>8-K - UNITEDHEALTH GROUP INC (0000731766) (Filer)</title>
    <link rel="alternate" href="https://www.sec.gov/Archives/edgar/data/731766/0001.htm"/>
    <updated>2024-05-01T12:00:00-04:00</updated>
    <summary type="html">Material event filing for the period.</summary>
  </entry>
  <entry>
    <title>8-K - UNITEDHEALTH GROUP INC (0000731766) (Filer)</title>
    <link rel="alternate" href="https://www.sec.gov/Archives/edgar/data/731766/0002.htm"/>
    <updated>2024-04-15T09:00:00-04:00</updated>
    <summary type="html">Another material event.</summary>
  </entry>
</feed>
"""

_CMS_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>CMS Newsroom</title>
  <item>
    <title>CMS releases updated Medicare Advantage data file</title>
    <link>https://www.cms.gov/news/1</link>
    <pubDate>Wed, 01 May 2024 10:00:00 +0000</pubDate>
    <description>New plan landscape data published.</description>
  </item>
</channel></rss>
"""


@responses.activate
def test_fetch_sec_parses_edgar_atom():
    url = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000731766&type=8-K&output=atom"
    responses.add(
        responses.GET,
        url,
        body=_SEC_ATOM,
        status=200,
        content_type="application/atom+xml",
    )
    source = SourceConfig(
        name="SEC EDGAR - UnitedHealth Group",
        type="sec",
        url=url,
        priority=4,
        tags=["financial", "sec"],
    )
    items = fetch_sec(source)
    assert len(items) == 2
    assert items[0].source_name == "SEC EDGAR - UnitedHealth Group"
    assert "UNITEDHEALTH" in items[0].title
    assert items[0].link.startswith("https://www.sec.gov/Archives/")


@responses.activate
def test_fetch_cms_parses_feed():
    url = "https://www.cms.gov/newsroom/rss"
    responses.add(
        responses.GET,
        url,
        body=_CMS_RSS,
        status=200,
        content_type="application/rss+xml",
    )
    source = SourceConfig(
        name="CMS Newsroom", type="cms", url=url, priority=5, tags=["cms"]
    )
    items = fetch_cms(source)
    assert len(items) == 1
    assert "Medicare Advantage data file" in items[0].title


@responses.activate
def test_fetcher_raises_on_http_error():
    """fetch_feed no longer swallows HTTP errors into a bare []  — that made a
    broken source indistinguishable from a quiet one. Isolating the failure
    (one bad source can't stop the run) is now main._fetch_one_source's job,
    not the fetcher's; see test_main_fetch.py."""
    url = "https://www.sec.gov/bad"
    responses.add(responses.GET, url, status=500)
    source = SourceConfig(name="SEC EDGAR - Bad", type="sec", url=url)
    with pytest.raises(requests.RequestException):
        fetch_sec(source)


_LITIGATION_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Star Ratings Archives - Health Care Litigation Tracker</title>
  <item>
    <title>Elevance Health Inc. et al. v. Department of Health and Human Services</title>
    <link>https://litigationtracker.law.georgetown.edu/litigation/elevance-v-hhs/</link>
    <pubDate>Thu, 02 Jul 2026 16:00:46 +0000</pubDate>
    <description>The post appeared first on Health Care Litigation Tracker.</description>
  </item>
  <item>
    <title>Zing Health Inc. et al. v. Department of Health and Human Services</title>
    <link>https://litigationtracker.law.georgetown.edu/litigation/zing-v-hhs/</link>
    <pubDate>Sun, 18 Jan 2026 05:00:00 +0000</pubDate>
    <description></description>
  </item>
</channel></rss>
"""


def _litigation_source(context="Medicare Advantage Star Ratings litigation."):
    return SourceConfig(
        name="Georgetown Litigation Tracker - MA Star Ratings",
        type="litigation",
        url="https://litigationtracker.law.georgetown.edu/issues/star-ratings/feed/",
        priority=4,
        tags=["litigation", "enforcement"],
        context=context,
    )


@responses.activate
def test_fetch_litigation_injects_context_into_summaries():
    from ma_signal_monitor.fetchers.litigation import fetch_litigation

    source = _litigation_source()
    responses.add(responses.GET, source.url, body=_LITIGATION_RSS, status=200)
    items = fetch_litigation(source)

    assert len(items) == 2
    # Context is prepended to a boilerplate summary...
    assert items[0].summary.startswith("Medicare Advantage Star Ratings litigation.")
    assert "appeared first on" in items[0].summary
    # ...and becomes the whole summary when the entry had none.
    assert items[1].summary == "Medicare Advantage Star Ratings litigation."


@responses.activate
def test_fetch_litigation_without_context_is_plain_rss():
    from ma_signal_monitor.fetchers.litigation import fetch_litigation

    source = _litigation_source(context="")
    responses.add(responses.GET, source.url, body=_LITIGATION_RSS, status=200)
    items = fetch_litigation(source)

    assert len(items) == 2
    assert "Medicare Advantage" not in items[0].summary
    assert items[1].summary == ""


def test_litigation_type_is_dispatched():
    from ma_signal_monitor.fetchers.litigation import fetch_litigation
    from ma_signal_monitor.main import _FETCHERS

    assert _FETCHERS["litigation"] is fetch_litigation
