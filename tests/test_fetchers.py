"""Tests for the SEC and CMS feed fetchers (delegating to the shared core)."""

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
def test_fetcher_handles_http_error_gracefully():
    url = "https://www.sec.gov/bad"
    responses.add(responses.GET, url, status=500)
    source = SourceConfig(name="SEC EDGAR - Bad", type="sec", url=url)
    assert fetch_sec(source) == []  # error isolated, returns empty
