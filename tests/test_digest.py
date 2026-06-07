"""Tests for the Daily Briefing digest."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from ma_signal_monitor import digest as digest_mod
from ma_signal_monitor.digest import (
    build_digest,
    generate_digest,
    render_html,
    render_text,
    send_digest,
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


def test_render_html_and_text_contain_stories(sample_config, temp_db):
    _seed_window(temp_db)
    d = build_digest(temp_db, sample_config, now=NOW)
    html = render_html(d, sample_config)
    text = render_text(d)
    assert "Star Ratings" in html and "Star Ratings" in text
    assert "California" in html  # state name rendered
    assert "<html" in html.lower()


def test_send_digest_skips_when_unconfigured(sample_config, temp_db):
    d = build_digest(temp_db, sample_config, now=NOW)
    # sample_config has no SMTP host -> best-effort skip, returns False.
    assert send_digest(d, "<html></html>", sample_config) is False


def test_send_digest_uses_smtp_when_configured(sample_config, temp_db):
    _seed_window(temp_db)
    sample_config.smtp_host = "smtp.example.com"
    sample_config.digest_from = "from@example.com"
    sample_config.digest_to = "a@example.com, b@example.com"
    d = build_digest(temp_db, sample_config, now=NOW)

    fake_smtp = MagicMock()
    with patch.object(digest_mod.smtplib, "SMTP") as smtp_cls:
        smtp_cls.return_value.__enter__.return_value = fake_smtp
        assert send_digest(d, render_html(d, sample_config), sample_config) is True
    fake_smtp.send_message.assert_called_once()


def test_generate_digest_persists_and_is_idempotent(sample_config, temp_db):
    _seed_window(temp_db)
    generate_digest(sample_config, temp_db, now=NOW, send=False)
    row = temp_db.get_latest_digest()
    assert row is not None
    assert row["digest_date"] == "2024-06-01"
    assert row["story_count"] == 2
    assert row["sent_at"] is None  # send=False
    # Re-running the same day replaces, not duplicates.
    generate_digest(sample_config, temp_db, now=NOW, send=False)
    assert len(temp_db.list_digests()) == 1


def test_briefing_page_renders(sample_config, temp_db):
    _seed_window(temp_db)
    generate_digest(sample_config, temp_db, now=NOW, send=False)
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
