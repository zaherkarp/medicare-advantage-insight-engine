"""Tests for keyword-candidate mining."""

from datetime import datetime

from ma_signal_monitor.keyword_mining import mine_keywords
from ma_signal_monitor.models import NormalizedItem, ScoredItem


def _label(temp_db, item_id, text, verdict):
    item = NormalizedItem(
        item_id=item_id,
        source_name="Test Feed",
        source_type="rss",
        source_priority=3,
        source_tags=["test"],
        title=text,
        link=f"https://example.com/{item_id}",
        published_date=datetime(2024, 1, 1, 12, 0),
        summary="",
    )
    temp_db.upsert_story(
        ScoredItem(item=item, relevance_score=0.5, matched_categories=["x"]),
        primary_category="x",
    )
    temp_db.add_feedback(item_id, verdict, channel="cli")


def _seed_labeled_set(temp_db, n=4):
    for i in range(n):
        _label(temp_db, f"pos{i}", "telehealth expansion drives growth", "relevant")
        _label(temp_db, f"neg{i}", "parking garage ribbon cutting event", "irrelevant")


def test_too_few_labels_returns_empty(sample_config, temp_db):
    _label(temp_db, "p", "telehealth growth", "relevant")
    # Only one positive, zero negatives → below min_docs.
    res = mine_keywords(temp_db, sample_config)
    assert res["inclusion"] == []
    assert res["exclusion"] == []


def test_surfaces_distinctive_terms(sample_config, temp_db):
    _seed_labeled_set(temp_db)
    res = mine_keywords(temp_db, sample_config)

    assert res["positives"] == 4
    assert res["negatives"] == 4
    incl = {c["term"] for c in res["inclusion"]}
    excl = {c["term"] for c in res["exclusion"]}
    assert "telehealth" in incl
    assert "parking" in excl
    # A term seen only in relevant docs has 0 irrelevant docs.
    tele = next(c for c in res["inclusion"] if c["term"] == "telehealth")
    assert tele["relevant_docs"] == 4
    assert tele["irrelevant_docs"] == 0


def test_excludes_existing_taxonomy_terms_from_inclusion(sample_config, temp_db):
    # "enrollment" is a taxonomy keyword in sample_config; even if it skews
    # positive it must not be suggested as a new inclusion keyword.
    for i in range(4):
        _label(temp_db, f"pos{i}", "enrollment surge reported", "relevant")
        _label(temp_db, f"neg{i}", "parking garage opened", "irrelevant")
    res = mine_keywords(temp_db, sample_config)
    incl = {c["term"] for c in res["inclusion"]}
    assert "enrollment" not in incl
