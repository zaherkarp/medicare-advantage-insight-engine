"""Tests for the payer intelligence pages and canonical entity grouping."""

from datetime import datetime
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
