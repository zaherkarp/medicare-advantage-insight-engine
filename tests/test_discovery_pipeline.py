"""Integration tests: discovery harvest in the pipeline + promotion merge."""

import responses

from ma_signal_monitor.config import load_config
from ma_signal_monitor.main import run
from ma_signal_monitor.storage import StateStore

# A story whose body links out to a new (non-source) domain.
_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Test Feed</title>
  <item>
    <title>UnitedHealthcare expands Medicare Advantage enrollment in California</title>
    <link>https://example.com/article/1</link>
    <pubDate>Mon, 01 Jan 2024 12:00:00 +0000</pubDate>
    <description><![CDATA[UnitedHealthcare grows Medicare Advantage enrollment and membership.
      See <a href="https://insightoutlet.test/report">deep analysis</a> and
      <a href="https://insightoutlet.test/data">the data</a>.]]></description>
  </item>
</channel></rss>
"""


@responses.activate
def test_pipeline_harvests_candidate_domains(sample_config, tmp_path):
    sample_config.discovery_enabled = True
    sample_config.discovery_min_story_score = 0.0
    responses.add(
        responses.GET,
        "https://example.com/feed",
        body=_RSS,
        status=200,
        content_type="application/rss+xml",
    )

    run(config=sample_config, project_root=tmp_path)

    store = StateStore(tmp_path / sample_config.db_path)
    try:
        domains = [
            r["domain"] for r in store.domains_due_for_discovery(min_times_seen=1)
        ]
        # The embedded outbound domain is captured; the source's own domain isn't.
        assert "insightoutlet.test" in domains
        assert "example.com" not in domains
    finally:
        store.close()


@responses.activate
def test_discovery_failure_does_not_break_ingestion(
    sample_config, tmp_path, monkeypatch
):
    sample_config.discovery_enabled = True
    responses.add(
        responses.GET,
        "https://example.com/feed",
        body=_RSS,
        status=200,
        content_type="application/rss+xml",
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("harvest exploded")

    monkeypatch.setattr("ma_signal_monitor.main._harvest_candidates", _boom)

    summary = run(config=sample_config, project_root=tmp_path)
    assert summary["errors"] == 0  # discovery failure is contained

    store = StateStore(tmp_path / sample_config.db_path)
    try:
        assert store.count_stories() == 1  # ingestion still completed
    finally:
        store.close()


def test_promoted_candidates_merge_into_sources(
    project_root_with_config, monkeypatch
):
    monkeypatch.setenv("DISCOVERY_ENABLED", "true")
    root = project_root_with_config

    store = StateStore(root / "data" / "state.db")
    store.upsert_candidate_source(
        feed_url="https://discovered.test/feed",
        domain="discovered.test",
        feed_title="Discovered Outlet",
        status="promoted",
    )
    store.close()

    config = load_config(root)
    assert config.discovery_enabled
    urls = [s.url for s in config.sources]
    assert "https://discovered.test/feed" in urls
    promoted = next(s for s in config.sources if s.url == "https://discovered.test/feed")
    assert promoted.name == "Discovered Outlet"
    assert "discovered" in promoted.tags


def test_merge_degrades_gracefully_without_db(project_root_with_config, monkeypatch):
    monkeypatch.setenv("DISCOVERY_ENABLED", "true")
    # No archive DB created — config load must not raise and uses YAML only.
    config = load_config(project_root_with_config)
    assert len(config.sources) == 1
