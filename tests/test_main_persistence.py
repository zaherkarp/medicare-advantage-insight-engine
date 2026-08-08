"""End-to-end test: the pipeline persists scored items into the story archive."""

from datetime import datetime

import responses

from ma_signal_monitor.main import _persist_stories, run
from ma_signal_monitor.models import NormalizedItem, ScoredItem
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

        # The run also logged this source's fetch outcome, end to end.
        health = store.get_source_fetch_health()
        entry = health[sample_config.sources[0].name]
        assert entry["last_status"] == "ok"
        assert entry["last_persisted_at"] is not None
    finally:
        store.close()


def _scored_item(source_name: str, title: str) -> ScoredItem:
    item = NormalizedItem(
        item_id=f"{source_name}:{title}",
        source_name=source_name,
        source_type="rss",
        source_priority=3,
        source_tags=[],
        title=title,
        link=f"https://example.com/{title}",
        published_date=datetime(2024, 1, 1, 12, 0),
        summary="",
    )
    return ScoredItem(item=item, relevance_score=0.05, reasons=[])


def test_persist_stories_counts_by_source(sample_config, temp_db):
    """_persist_stories reports per-source counts so a source that fetches
    fine but silently fails to archive (persisted << fetched) is visible."""
    scored = [
        _scored_item("Source A", "A1"),
        _scored_item("Source A", "A2"),
        _scored_item("Source B", "B1"),
    ]
    counts = _persist_stories(scored, alerts=[], config=sample_config, store=temp_db)
    assert counts == {"Source A": 2, "Source B": 1}
    assert temp_db.count_stories() == 3
