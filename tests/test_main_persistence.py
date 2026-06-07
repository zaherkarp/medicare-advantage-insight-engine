"""End-to-end test: the pipeline persists scored items into the story archive."""

import responses

from ma_signal_monitor.main import run
from ma_signal_monitor.storage import StateStore

_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Test Feed</title>
  <item>
    <title>UnitedHealthcare expands Medicare Advantage enrollment in California</title>
    <link>https://example.com/article/1</link>
    <pubDate>Mon, 01 Jan 2024 12:00:00 +0000</pubDate>
    <description>UnitedHealthcare grows enrollment and membership across California counties.</description>
  </item>
  <item>
    <title>CMS proposes Star Ratings rule</title>
    <link>https://example.com/article/2</link>
    <pubDate>Tue, 02 Jan 2024 08:00:00 +0000</pubDate>
    <description>CMS released a proposed rule on Star Ratings and risk adjustment.</description>
  </item>
</channel></rss>
"""


@responses.activate
def test_pipeline_persists_stories(sample_config, tmp_path):
    responses.add(
        responses.GET,
        "https://example.com/feed",
        body=_RSS,
        status=200,
        content_type="application/rss+xml",
    )

    summary = run(config=sample_config, project_root=tmp_path)
    assert summary["items_fetched"] == 2

    # Reopen the archive DB the pipeline wrote and confirm stories landed.
    store = StateStore(tmp_path / sample_config.db_path)
    try:
        assert store.count_stories() == 2
        titles = [r["title"] for r in store.get_stories()]
        assert any("UnitedHealthcare" in t for t in titles)
        # California should be detected from the first story's content.
        assert store.count_stories(state="CA") == 1
    finally:
        store.close()
