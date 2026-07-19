"""Tests for the payer intelligence pages and canonical entity grouping."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from ma_signal_monitor.models import NormalizedItem, ScoredItem
from ma_signal_monitor.payers import ALIAS_TO_GROUP, PAYER_GROUPS, get_group
from ma_signal_monitor.web.app import create_app

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _seed_story(
    store,
    item_id,
    title,
    *,
    category="membership_movement",
    score=0.6,
    states=None,
    entities=None,
    source_name="Test Feed",
    published=datetime(2024, 1, 1, 12, 0),
):
    item = NormalizedItem(
        item_id=item_id,
        source_name=source_name,
        source_type="rss",
        source_priority=4,
        source_tags=["test"],
        title=title,
        link=f"https://example.com/{item_id}",
        published_date=published,
        summary=f"Summary for {title}.",
    )
    scored = ScoredItem(
        item=item,
        relevance_score=score,
        matched_categories=[category],
        matched_entities=entities or [],
    )
    store.upsert_story(scored, primary_category=category, states=states or [])


# --- Canonical grouping ---


def test_slugs_and_aliases_are_unique():
    slugs = [g.slug for g in PAYER_GROUPS]
    assert len(slugs) == len(set(slugs))
    aliases = [a for g in PAYER_GROUPS for a in g.aliases]
    assert len(aliases) == len(set(aliases))


def test_every_watched_entity_has_a_group():
    """Adding a watched entity to taxonomy.yaml requires a payer group."""
    taxonomy = yaml.safe_load((_PROJECT_ROOT / "config/taxonomy.yaml").read_text())
    missing = [e for e in taxonomy["watched_entities"] if e not in ALIAS_TO_GROUP]
    assert not missing, f"watched_entities without a payer group: {missing}"


def test_get_group():
    assert get_group("humana").name == "Humana"
    assert get_group("not-a-payer") is None
    uhc = get_group("unitedhealthcare")
    assert "UnitedHealth" in uhc.aliases and "UHC" in uhc.aliases


# --- Storage entity filters ---


def test_entity_filter_and_counts(temp_db):
    _seed_story(temp_db, "s1", "UHG expands", entities=["UnitedHealth", "UHC"])
    _seed_story(temp_db, "s2", "Humana update", entities=["Humana"])
    _seed_story(temp_db, "s3", "No payer story")

    rows = temp_db.get_stories(entity_aliases=["UnitedHealth", "UHC"])
    assert [r["item_id"] for r in rows] == ["s1"]
    assert temp_db.count_stories(entity_aliases=["Humana"]) == 1
    assert temp_db.count_stories(entity_aliases=["Aetna"]) == 0

    counts = temp_db.get_entity_counts()
    assert counts == {"UnitedHealth": 1, "UHC": 1, "Humana": 1}


def test_entity_filter_respects_min_score(temp_db):
    _seed_story(temp_db, "lo", "Low score", entities=["Humana"], score=0.05)
    assert temp_db.count_stories(entity_aliases=["Humana"], min_score=0.1) == 0
    assert temp_db.get_entity_counts(min_score=0.1) == {}


def test_source_prefix_filter(temp_db):
    _seed_story(
        temp_db,
        "sec1",
        "8-K filing",
        entities=["Humana"],
        source_name="SEC EDGAR - Humana",
    )
    _seed_story(temp_db, "n1", "News story", entities=["Humana"])
    rows = temp_db.get_stories(entity_aliases=["Humana"], source_prefix="SEC EDGAR")
    assert [r["item_id"] for r in rows] == ["sec1"]


def test_entity_stats(temp_db):
    _seed_story(
        temp_db,
        "s1",
        "UHG in Texas",
        entities=["UnitedHealth"],
        states=["TX"],
        category="membership_movement",
    )
    _seed_story(
        temp_db,
        "s2",
        "UHC ruling",
        entities=["UHC"],
        states=["TX", "CA"],
        category="policy_regulatory",
    )
    stats = temp_db.get_entity_stats(["UnitedHealth", "UHC"])
    assert stats["total"] == 2
    assert stats["categories"] == {"membership_movement": 1, "policy_regulatory": 1}
    assert stats["states"] == {"TX": 2, "CA": 1}


def test_weekly_counts_buckets_by_entity(temp_db):
    now = datetime(2024, 3, 20, 12, 0)  # a Wednesday
    _seed_story(temp_db, "w1", "UHG A", entities=["UnitedHealth"], published=now)
    _seed_story(
        temp_db,
        "w2",
        "UHG B",
        entities=["UHC"],
        published=now - timedelta(days=1),  # same week
    )
    _seed_story(
        temp_db,
        "w3",
        "UHG old",
        entities=["UnitedHealth"],
        published=now - timedelta(weeks=2),
    )
    _seed_story(temp_db, "h1", "Humana", entities=["Humana"], published=now)

    series = temp_db.get_weekly_counts(
        weeks=4, entity_aliases=["UnitedHealth", "UHC"], now=now
    )
    assert len(series) == 4
    assert [w["count"] for w in series] == [0, 1, 0, 2]  # oldest → newest
    # Humana story is excluded by the entity filter.
    assert sum(w["count"] for w in series) == 3


def test_daily_counts_buckets_by_entity(temp_db):
    now = datetime(2024, 3, 20, 12, 0)
    _seed_story(temp_db, "d1", "UHG A", entities=["UnitedHealth"], published=now)
    _seed_story(
        temp_db, "d2", "UHG B", entities=["UHC"], published=now - timedelta(days=1)
    )
    _seed_story(
        temp_db,
        "d3",
        "UHG old",
        entities=["UnitedHealth"],
        published=now - timedelta(days=40),  # outside a 7-day window
    )
    _seed_story(temp_db, "h1", "Humana", entities=["Humana"], published=now)

    series = temp_db.get_daily_counts(
        days=7, entity_aliases=["UnitedHealth", "UHC"], now=now
    )
    assert len(series) == 7
    # d2 lands yesterday, d1 today; cross-alias match folds UHC + UnitedHealth.
    assert [d["count"] for d in series] == [0, 0, 0, 0, 0, 1, 1]
    assert series[-1]["day"] == "2024-03-20"
    # Humana and the out-of-window UHG story are excluded.
    assert sum(d["count"] for d in series) == 2


def test_daily_counts_buckets_by_category(temp_db):
    now = datetime(2024, 3, 20, 12, 0)
    _seed_story(temp_db, "c1", "Policy A", category="policy_regulatory", published=now)
    _seed_story(
        temp_db,
        "c2",
        "Policy B",
        category="policy_regulatory",
        published=now - timedelta(days=2),
    )
    _seed_story(
        temp_db, "m1", "Membership", category="membership_movement", published=now
    )

    series = temp_db.get_daily_counts(days=7, category="policy_regulatory", now=now)
    assert [d["count"] for d in series] == [0, 0, 0, 0, 1, 0, 1]  # c2 (−2d), c1 (today)
    # The membership_movement story is excluded by the category filter.
    assert sum(d["count"] for d in series) == 2


def test_daily_counts_dateless_story_buckets_by_fetched_at(temp_db):
    # No published_date → fetched_at (set at upsert time ≈ now) drives the bucket.
    _seed_story(temp_db, "nd", "No date", entities=["Humana"], published=None)
    now = datetime.utcnow()
    series = temp_db.get_daily_counts(days=7, entity_aliases=["Humana"], now=now)
    assert series[-1]["count"] == 1  # today's (last) bucket
    assert sum(d["count"] for d in series) == 1


def test_daily_counts_hides_duplicates_and_respects_min_score(temp_db):
    now = datetime(2024, 3, 20, 12, 0)
    _seed_story(temp_db, "rep", "Humana rep", entities=["Humana"], published=now)
    # A near-duplicate carried by another source must not double-count.
    dup = NormalizedItem(
        item_id="dup",
        source_name="Other Feed",
        source_type="rss",
        source_priority=4,
        source_tags=["test"],
        title="Humana dup",
        link="https://example.com/dup",
        published_date=now,
        summary="dup",
    )
    temp_db.upsert_story(
        ScoredItem(
            item=dup,
            relevance_score=0.6,
            matched_categories=["membership_movement"],
            matched_entities=["Humana"],
        ),
        primary_category="membership_movement",
        duplicate_of="rep",
    )
    # A sub-floor story the min_score gate must drop.
    _seed_story(
        temp_db, "lo", "Humana low", entities=["Humana"], score=0.05, published=now
    )

    series = temp_db.get_daily_counts(
        days=7, entity_aliases=["Humana"], min_score=0.1, now=now
    )
    assert sum(d["count"] for d in series) == 1  # only the representative story


# --- Web routes ---


@pytest.fixture
def client(sample_config, temp_db):
    _seed_story(
        temp_db,
        "story-uhc",
        "UnitedHealthcare expands enrollment in California",
        entities=["UnitedHealthcare"],
        states=["CA"],
    )
    _seed_story(
        temp_db,
        "story-sec",
        "8-K: UnitedHealth Group results",
        entities=["UnitedHealth"],
        source_name="SEC EDGAR - UnitedHealth Group",
        category="financial_pressure",
        published=datetime(2024, 3, 1, 9, 0),
    )
    _seed_story(
        temp_db,
        "story-humana",
        "Humana update on benefits",
        entities=["Humana"],
    )
    app = create_app(sample_config, temp_db)
    return TestClient(app)


def test_payers_overview(client):
    resp = client.get("/payers")
    assert resp.status_code == 200
    assert "Payer Intelligence" in resp.text
    assert "UnitedHealthcare" in resp.text
    # Aliases fold into one organization: 2 UHG signals.
    assert "2\n              signals" in resp.text or "2 signals" in resp.text.replace(
        "\n              ", " "
    )
    # Section headings present.
    assert "Brokerage &amp; Distribution" in resp.text


def test_payer_detail_lists_group_stories(client):
    resp = client.get("/payers/unitedhealthcare")
    assert resp.status_code == 200
    assert "enrollment in California" in resp.text
    assert "8-K: UnitedHealth Group results" in resp.text
    assert "Humana update" not in resp.text
    # Overview panels.
    assert "Signal mix" in resp.text
    assert "State footprint" in resp.text
    assert "Recent SEC filings" in resp.text


def test_payer_detail_unknown_slug_404(client):
    assert client.get("/payers/not-a-payer").status_code == 404


def test_payer_detail_without_sec_filings(client):
    resp = client.get("/payers/humana")
    assert resp.status_code == 200
    assert "Humana update" in resp.text
    assert "Recent SEC filings" not in resp.text


def test_payer_detail_shows_recent_signal_volume(sample_config, temp_db):
    # A story within the 12-week window drives the sparkline panel.
    _seed_story(
        temp_db,
        "recent",
        "Cigna reprices its Medicare Advantage plans",
        entities=["Cigna"],
        published=datetime.utcnow(),
    )
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/payers/cigna")
    assert resp.status_code == 200
    assert "Signal volume" in resp.text
    assert "<svg" in resp.text and "spark-line" in resp.text


def test_payer_detail_no_signal_volume_when_stale(client):
    # UnitedHealthcare's seeded stories are dated 2024 — outside the window,
    # so the Signal volume panel is absent (other panels still render).
    resp = client.get("/payers/unitedhealthcare")
    assert "Signal mix" in resp.text  # other panels present
    assert "Signal volume" not in resp.text


def test_payer_detail_cards_show_coverage_timeline_and_picker(sample_config, temp_db):
    now = datetime.utcnow()
    _seed_story(
        temp_db, "cig1", "Cigna reprices plans", entities=["Cigna"], published=now
    )
    _seed_story(
        temp_db,
        "cig2",
        "Cigna expands network",
        entities=["Cigna"],
        published=now - timedelta(days=2),
    )
    client = TestClient(create_app(sample_config, temp_db))
    resp = client.get("/payers/cigna")
    assert resp.status_code == 200
    # Per-card timelines render alongside the existing weekly Signal-volume panel.
    assert 'class="sparkline card-timeline"' in resp.text
    # The window picker preserves the payer path.
    assert 'href="/payers/cigna?days=14"' in resp.text
