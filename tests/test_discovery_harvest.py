"""Tests for the pure link/domain harvesting logic."""

from ma_signal_monitor.config import SourceConfig
from ma_signal_monitor.discovery.harvest import (
    StoryLinks,
    configured_source_domains,
    domain_of,
    extract_links,
    harvest_domains,
)


def test_extract_links_resolves_and_filters():
    html = (
        '<p>See <a href="https://a.example/report">report</a> and '
        '<a href="/relative/path">local</a> and '
        '<a href="mailto:x@y.com">mail</a> and '
        '<a href="#frag">frag</a>.</p>'
    )
    links = extract_links(html, base_url="https://news.test/story/1")
    assert "https://a.example/report" in links
    # Relative href is resolved against the story URL.
    assert "https://news.test/relative/path" in links
    # mailto + fragment-only links are dropped.
    assert not any(link.startswith("mailto") for link in links)
    assert not any("#frag" in link for link in links)


def test_domain_of_normalizes():
    assert domain_of("https://www.Example.com/path?x=1") == "example.com"
    assert domain_of("http://sub.example.org/") == "sub.example.org"
    assert domain_of("not a url") == ""


def test_harvest_aggregates_with_relevance_weighting():
    stories = [
        StoryLinks(
            item_id="s1",
            link="https://src.test/a",
            content_html='<a href="https://insight.test/x">x</a>',
            relevance_score=0.8,
        ),
        StoryLinks(
            item_id="s2",
            link="https://src.test/b",
            content_html='<a href="https://insight.test/y">y</a>',
            relevance_score=0.5,
        ),
    ]
    stats = harvest_domains(stories, min_score=0.3)
    assert "insight.test" in stats
    stat = stats["insight.test"]
    assert stat.times_seen == 2
    assert abs(stat.relevance_score - 1.3) < 1e-9  # 0.8 + 0.5


def test_harvest_respects_min_score_and_exclusions():
    stories = [
        # Below threshold — ignored entirely.
        StoryLinks(
            "s0", "https://src.test/0", '<a href="https://low.test/p">p</a>', 0.1
        ),
        StoryLinks(
            "s1",
            "https://src.test/1",
            # own domain + a configured source + a social link are all skipped.
            '<a href="https://src.test/self">self</a>'
            '<a href="https://known.test/feed">known</a>'
            '<a href="https://twitter.com/foo">tw</a>'
            '<a href="https://good.test/article">good</a>',
            0.9,
        ),
    ]
    exclude = {"known.test"}
    stats = harvest_domains(stories, min_score=0.3, exclude_domains=exclude)
    assert set(stats) == {"good.test"}


def test_harvest_counts_domain_once_per_story():
    stories = [
        StoryLinks(
            "s1",
            "https://src.test/1",
            '<a href="https://dup.test/a">a</a><a href="https://dup.test/b">b</a>',
            0.7,
        )
    ]
    stats = harvest_domains(stories, min_score=0.0)
    assert stats["dup.test"].times_seen == 1


def test_configured_source_domains():
    sources = [
        SourceConfig(
            name="A", type="rss", url="https://a.test/feed", homepage="https://a.test/"
        ),
        SourceConfig(name="B", type="rss", url="https://www.b.test/rss"),
    ]
    domains = configured_source_domains(sources)
    assert domains == {"a.test", "b.test"}
