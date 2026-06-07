"""Tests for the static GitHub Pages export."""

import json
from datetime import datetime

from ma_signal_monitor.digest import generate_digest
from ma_signal_monitor.models import NormalizedItem, ScoredItem
from ma_signal_monitor.static_export import build_site


def _seed(store, item_id, title, *, category, states=None):
    item = NormalizedItem(
        item_id=item_id,
        source_name="Healthcare Dive",
        source_type="rss",
        source_priority=3,
        source_tags=["industry"],
        title=title,
        link=f"https://example.com/{item_id}",
        published_date=datetime(2024, 1, 1, 12, 0),
        summary=f"{title} summary.",
    )
    store.upsert_story(
        ScoredItem(item=item, relevance_score=0.7, matched_categories=[category]),
        primary_category=category,
        states=states or [],
    )


def _build(tmp_path, sample_config, store, base="/myrepo"):
    out = tmp_path / "site"
    counts = build_site(store, sample_config, out, base_path=base)
    return out, counts


def test_build_creates_expected_pages(tmp_path, sample_config, temp_db):
    _seed(temp_db, "a1", "CMS Star Ratings rule", category="policy_regulatory")
    _seed(
        temp_db,
        "b2",
        "UHC enrollment in Texas",
        category="membership_movement",
        states=["TX"],
    )
    generate_digest(sample_config, temp_db, now=datetime(2024, 1, 1, 12, 0), send=False)

    out, counts = _build(tmp_path, sample_config, temp_db)

    assert (out / "index.html").exists()
    assert (out / "sources.html").exists()
    assert (out / "states.html").exists()
    assert (out / "status.html").exists()
    assert (out / "briefing.html").exists()
    assert (out / "search.html").exists()
    assert (out / "search-index.json").exists()
    assert (out / "static" / "style.css").exists()
    assert (out / ".nojekyll").exists()
    assert (out / "story" / "a1.html").exists()
    assert (out / "story" / "b2.html").exists()
    assert (out / "topics" / "policy_regulatory.html").exists()
    assert (out / "states" / "TX.html").exists()
    assert (out / "briefing" / "2024-01-01.html").exists()
    assert counts["stories"] == 2


def test_links_are_rewritten_with_base_path(tmp_path, sample_config, temp_db):
    _seed(temp_db, "a1", "CMS Star Ratings rule", category="policy_regulatory")
    out, _ = _build(tmp_path, sample_config, temp_db, base="/myrepo")

    index = (out / "index.html").read_text()
    # Internal links and assets are base-prefixed and point at .html files.
    assert "/myrepo/story/a1.html" in index
    assert "/myrepo/static/style.css" in index
    # No bare server-style root links remain.
    assert 'href="/story/' not in index
    assert 'href="/static/' not in index
    # External source links are untouched.
    story = (out / "story" / "a1.html").read_text()
    assert "https://example.com/a1" in story


def test_search_index_contents(tmp_path, sample_config, temp_db):
    _seed(temp_db, "a1", "CMS Star Ratings rule", category="policy_regulatory")
    out, _ = _build(tmp_path, sample_config, temp_db, base="/myrepo")

    data = json.loads((out / "search-index.json").read_text())
    assert len(data) == 1
    entry = data[0]
    assert entry["title"] == "CMS Star Ratings rule"
    assert entry["url"] == "/myrepo/story/a1.html"
    assert entry["category"] == "Policy / Regulatory Changes"
    # The client-side search page references its index.
    assert "search-index.json" in (out / "search.html").read_text()


def test_root_base_path(tmp_path, sample_config, temp_db):
    _seed(temp_db, "a1", "A story", category="policy_regulatory")
    out, _ = _build(tmp_path, sample_config, temp_db, base="")
    index = (out / "index.html").read_text()
    assert "/story/a1.html" in index
    assert "/static/style.css" in index
