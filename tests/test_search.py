"""Tests for full-text search (FTS5 with LIKE fallback)."""

from datetime import datetime

from fastapi.testclient import TestClient

from ma_signal_monitor.models import NormalizedItem, ScoredItem
from ma_signal_monitor.web.app import create_app


def _seed(
    store,
    item_id,
    title,
    summary="",
    category="policy_regulatory",
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
        published_date=datetime(2024, 1, 1, 12, 0),
        summary=summary,
    )
    store.upsert_story(
        ScoredItem(
            item=item,
            relevance_score=0.6,
            matched_categories=[category],
            matched_entities=entities or [],
        ),
        primary_category=category,
        states=states or [],
    )


def _seed_corpus(store):
    _seed(
        store,
        "s1",
        "CMS finalizes 2027 Star Ratings methodology",
        summary="New cut points for Medicare Advantage plans.",
    )
    _seed(
        store,
        "s2",
        "UnitedHealthcare expands enrollment in Texas",
        summary="The payer adds Medicare Advantage members.",
        category="membership_movement",
        states=["TX"],
        entities=["UnitedHealthcare"],
    )
    _seed(
        store,
        "s3",
        "Humana flags margin pressure",
        summary="Rising medical costs weigh on results.",
        category="financial_pressure",
        entities=["Humana"],
    )


def test_fts_is_enabled(temp_db):
    # Modern SQLite ships FTS5; this documents the expectation.
    assert temp_db.fts_enabled is True


def test_search_matches_title_keyword(temp_db):
    _seed_corpus(temp_db)
    rows = temp_db.search_stories("Star Ratings")
    assert len(rows) == 1
    assert rows[0]["item_id"] == "s1"


def test_search_matches_summary_and_is_prefix(temp_db):
    _seed_corpus(temp_db)
    # "enroll" should prefix-match "enrollment".
    rows = temp_db.search_stories("enroll")
    assert {r["item_id"] for r in rows} == {"s2"}


def test_search_multiple_terms_are_anded(temp_db):
    _seed_corpus(temp_db)
    assert {r["item_id"] for r in temp_db.search_stories("Humana margin")} == {"s3"}
    assert temp_db.search_stories("Humana Texas") == []  # no single story has both


def test_count_search(temp_db):
    _seed_corpus(temp_db)
    assert temp_db.count_search("Medicare") == 2  # s1 + s2 summaries
    assert temp_db.count_search("nonexistentword") == 0


def test_empty_query_returns_nothing(temp_db):
    _seed_corpus(temp_db)
    assert temp_db.search_stories("   ") == []
    assert temp_db.count_search("") == 0


def test_like_fallback_when_fts_disabled(temp_db):
    _seed_corpus(temp_db)
    temp_db.fts_enabled = False  # force the fallback path
    rows = temp_db.search_stories("Humana")
    assert {r["item_id"] for r in rows} == {"s3"}
    assert temp_db.count_search("Humana") == 1


def _seed_scored(store, item_id, title, summary, score):
    """Seed a story at an explicit relevance score (for floor tests)."""
    item = NormalizedItem(
        item_id=item_id,
        source_name="Virginia Mercury",
        source_type="rss",
        source_priority=2,
        source_tags=["state"],
        title=title,
        link=f"https://example.com/{item_id}",
        published_date=datetime(2024, 1, 1, 12, 0),
        summary=summary,
    )
    store.upsert_story(
        ScoredItem(item=item, relevance_score=score, matched_categories=[]),
        primary_category="uncategorized",
    )


def test_search_respects_min_score(temp_db):
    # Two stories share the term "Medicare"; one is sub-floor noise.
    _seed(
        temp_db,
        "hi",
        "Medicare Advantage Star Ratings",
        summary="A real signal.",
    )  # score 0.6
    _seed_scored(
        temp_db, "lo", "Medicare mentioned in a local column", "Off-topic.", 0.04
    )
    assert temp_db.count_search("Medicare") == 2  # archive keeps both
    assert temp_db.count_search("Medicare", min_score=0.1) == 1
    rows = temp_db.search_stories("Medicare", min_score=0.1)
    assert {r["item_id"] for r in rows} == {"hi"}


def test_search_min_score_like_fallback(temp_db):
    _seed(temp_db, "hi", "Medicare Advantage Star Ratings", summary="A real signal.")
    _seed_scored(
        temp_db, "lo", "Medicare mentioned in a local column", "Off-topic.", 0.04
    )
    temp_db.fts_enabled = False  # force the LIKE path
    assert temp_db.count_search("Medicare", min_score=0.1) == 1
    rows = temp_db.search_stories("Medicare", min_score=0.1)
    assert {r["item_id"] for r in rows} == {"hi"}


def test_upsert_keeps_fts_in_sync(temp_db):
    _seed(temp_db, "u1", "Original title about benchmarks")
    assert len(temp_db.search_stories("benchmarks")) == 1
    # Re-upsert same id with new text; stale term should disappear.
    _seed(temp_db, "u1", "Revised title about telehealth")
    assert temp_db.search_stories("benchmarks") == []
    assert len(temp_db.search_stories("telehealth")) == 1


def test_search_filtered_combines_keyword_and_category(temp_db):
    _seed_corpus(temp_db)
    # "Medicare" matches s1 and s2's summaries; the category filter narrows
    # to just s1 — exactly the combination neither search_stories() (no
    # category filter) nor get_stories() (no keyword search) covers alone.
    rows = temp_db.search_stories_filtered("Medicare", category="policy_regulatory")
    assert {r["item_id"] for r in rows} == {"s1"}


def test_search_filtered_respects_state_and_entity_aliases(temp_db):
    _seed_corpus(temp_db)
    rows = temp_db.search_stories_filtered(
        "Medicare", state="TX", entity_aliases=["UnitedHealthcare"]
    )
    assert {r["item_id"] for r in rows} == {"s2"}
    assert temp_db.search_stories_filtered("Medicare", state="CA") == []


def test_search_filtered_since(temp_db):
    _seed_corpus(temp_db)  # all published 2024-01-01
    assert len(temp_db.search_stories_filtered("Medicare", since="2024-01-01")) == 2
    assert temp_db.search_stories_filtered("Medicare", since="2024-06-01") == []


def test_search_filtered_min_score(temp_db):
    _seed(temp_db, "hi", "Medicare Advantage Star Ratings", summary="A real signal.")
    _seed_scored(
        temp_db, "lo", "Medicare mentioned in a local column", "Off-topic.", 0.04
    )
    rows = temp_db.search_stories_filtered("Medicare", min_score=0.1)
    assert {r["item_id"] for r in rows} == {"hi"}


def test_count_search_filtered_matches_row_count(temp_db):
    _seed_corpus(temp_db)
    assert temp_db.count_search_filtered("Medicare", category="policy_regulatory") == 1
    assert temp_db.count_search_filtered("Medicare") == 2


def test_search_filtered_like_fallback(temp_db):
    _seed_corpus(temp_db)
    temp_db.fts_enabled = False
    rows = temp_db.search_stories_filtered("Medicare", category="policy_regulatory")
    assert {r["item_id"] for r in rows} == {"s1"}
    assert temp_db.count_search_filtered("Medicare", category="policy_regulatory") == 1


def test_search_filtered_empty_query_returns_nothing(temp_db):
    _seed_corpus(temp_db)
    assert temp_db.search_stories_filtered("   ") == []
    assert temp_db.count_search_filtered("   ") == 0


def test_search_route(sample_config, temp_db):
    _seed_corpus(temp_db)
    client = TestClient(create_app(sample_config, temp_db))

    resp = client.get("/search", params={"q": "Star Ratings"})
    assert resp.status_code == 200
    assert "Star Ratings methodology" in resp.text
    assert "margin pressure" not in resp.text  # the Humana story is excluded

    # Empty query just renders the form.
    blank = client.get("/search")
    assert blank.status_code == 200
    assert "<form" in blank.text
