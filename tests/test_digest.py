"""Tests for the Daily Briefing digest."""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from ma_signal_monitor.digest import (
    build_digest,
    generate_digest,
    render_html,
)
from ma_signal_monitor.models import NormalizedItem, ScoredItem
from ma_signal_monitor.web.app import create_app

NOW = datetime(2024, 6, 1, 12, 0, 0)


def _seed(
    store,
    item_id,
    title,
    *,
    category,
    score,
    published,
    summary="A summary.",
    states=None,
    entities=None,
):
    item = NormalizedItem(
        item_id=item_id,
        source_name="Healthcare Dive",
        source_type="rss",
        source_priority=3,
        source_tags=["industry"],
        title=title,
        link=f"https://example.com/{item_id}",
        published_date=published,
        summary=summary,
    )
    scored = ScoredItem(
        item=item,
        relevance_score=score,
        matched_categories=[category],
        matched_entities=entities or [],
    )
    store.upsert_story(scored, primary_category=category, states=states or [])


def _seed_window(store):
    """Two in-window stories (one high, one mid), plus out-of-window/low-score."""
    _seed(
        store,
        "hi",
        "CMS finalizes Star Ratings rule",
        category="policy_regulatory",
        score=0.9,
        published=NOW - timedelta(hours=2),
        states=["TX"],
    )
    _seed(
        store,
        "mid",
        "UnitedHealthcare grows enrollment in California",
        category="membership_movement",
        score=0.6,
        published=NOW - timedelta(hours=5),
        states=["CA"],
        entities=["UnitedHealthcare"],
    )
    # Below digest_min_score (0.3) -> excluded.
    _seed(
        store,
        "low",
        "Minor item",
        category="competitive_strategy",
        score=0.1,
        published=NOW - timedelta(hours=1),
    )
    # Outside the 24h window -> excluded.
    _seed(
        store,
        "old",
        "Old news",
        category="policy_regulatory",
        score=0.95,
        published=NOW - timedelta(hours=48),
    )


def test_build_digest_selects_in_window_top_stories(sample_config, temp_db):
    _seed_window(temp_db)
    d = build_digest(temp_db, sample_config, now=NOW)
    assert d.story_count == 2
    titles = [s.title for sec in d.sections for s in sec[1]]
    assert "CMS finalizes Star Ratings rule" in titles
    assert "Minor item" not in titles  # below min score
    assert "Old news" not in titles  # outside window


def test_build_digest_groups_by_category_in_order(sample_config, temp_db):
    _seed_window(temp_db)
    d = build_digest(temp_db, sample_config, now=NOW)
    labels = [label for label, _ in d.sections]
    # policy_regulatory is ordered before membership_movement.
    assert labels.index("Policy / Regulatory Changes") < labels.index(
        "Membership Movement"
    )


def test_render_html_contains_stories(sample_config, temp_db):
    _seed_window(temp_db)
    d = build_digest(temp_db, sample_config, now=NOW)
    html = render_html(d, sample_config)
    assert "Star Ratings" in html
    assert "California" in html  # state name rendered
    assert "<html" in html.lower()


def test_digest_lede_present_and_rendered(sample_config, temp_db):
    _seed_window(temp_db)
    d = build_digest(temp_db, sample_config, now=NOW)
    assert d.lede is not None
    assert d.lede.total == 2  # full window count (both in-window stories)
    html = render_html(d, sample_config)
    assert "What's happening" in html


def test_digest_lede_absent_for_empty_window(sample_config, temp_db):
    d = build_digest(temp_db, sample_config, now=NOW)  # nothing seeded
    assert d.lede is None
    assert "What's happening" not in render_html(d, sample_config)


def test_digest_lede_can_be_disabled(sample_config, temp_db):
    _seed_window(temp_db)
    sample_config.digest_lede_enabled = False
    d = build_digest(temp_db, sample_config, now=NOW)
    assert d.lede is None


def test_view_online_link_uses_public_base_url(sample_config, temp_db):
    """The briefing renders in an iframe, so its links must be absolute and
    must point at the configured site, not a stale default."""
    _seed_window(temp_db)
    sample_config.public_base_url = "https://signal.zaherkarp.com"
    html = render_html(build_digest(temp_db, sample_config, now=NOW), sample_config)
    assert 'href="https://signal.zaherkarp.com/briefing"' in html
    assert "github.io" not in html


def test_generate_digest_persists_and_is_idempotent(sample_config, temp_db):
    _seed_window(temp_db)
    generate_digest(sample_config, temp_db, now=NOW)
    row = temp_db.get_latest_digest()
    assert row is not None
    assert row["digest_date"] == "2024-06-01"
    assert row["story_count"] == 2
    assert row["sent_at"] is None  # historical column, never written
    # Re-running the same day replaces, not duplicates.
    generate_digest(sample_config, temp_db, now=NOW)
    assert len(temp_db.list_digests()) == 1


def test_briefing_page_renders(sample_config, temp_db):
    _seed_window(temp_db)
    generate_digest(sample_config, temp_db, now=NOW)
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/briefing")
    assert resp.status_code == 200
    assert "Daily Briefing" in resp.text
    assert "2024-06-01" in resp.text  # archive list entry

    by_date = client.get("/briefing/2024-06-01")
    assert by_date.status_code == 200
    assert client.get("/briefing/1999-01-01").status_code == 404


def test_briefing_empty_state(sample_config, temp_db):
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/briefing")
    assert resp.status_code == 200
    assert "No briefing has been generated yet" in resp.text
