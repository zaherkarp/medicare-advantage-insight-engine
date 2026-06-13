"""Tests for the scorer-vs-reader disagreement digest."""

from datetime import datetime

from ma_signal_monitor.disagreement import find_disagreements
from ma_signal_monitor.models import NormalizedItem, ScoredItem


def _row(item_id, score, verdict, source="Test Feed"):
    return {
        "item_id": item_id,
        "title": f"Story {item_id}",
        "link": f"https://example.com/{item_id}",
        "source": source,
        "score": score,
        "verdict": verdict,
    }


# --- Pure partitioning logic ---


def test_over_scored_is_high_score_marked_irrelevant():
    rows = [_row("a", 0.8, "irrelevant")]
    res = find_disagreements(rows, threshold=0.3)
    assert [e["item_id"] for e in res["over_scored"]] == ["a"]
    assert res["under_scored"] == []
    assert res["over_scored"][0]["gap"] == 0.5  # 0.8 - 0.3


def test_under_scored_is_low_score_marked_relevant():
    rows = [_row("b", 0.1, "relevant"), _row("c", 0.05, "great")]
    res = find_disagreements(rows, threshold=0.3)
    # "great" counts as positive, same as "relevant".
    assert {e["item_id"] for e in res["under_scored"]} == {"b", "c"}
    assert res["over_scored"] == []


def test_agreement_produces_no_disagreements():
    rows = [
        _row("hit", 0.9, "relevant"),  # scorer high, owner relevant → agree
        _row("miss", 0.05, "irrelevant"),  # scorer low, owner irrelevant → agree
    ]
    res = find_disagreements(rows, threshold=0.3)
    assert res["over_scored"] == []
    assert res["under_scored"] == []
    assert res["labeled"] == 2


def test_boundary_score_counts_as_scorer_relevant():
    # score == threshold means the scorer cleared it, so an irrelevant verdict
    # is an over-score disagreement (gap 0).
    rows = [_row("edge", 0.3, "irrelevant")]
    res = find_disagreements(rows, threshold=0.3)
    assert [e["item_id"] for e in res["over_scored"]] == ["edge"]
    assert res["over_scored"][0]["gap"] == 0.0


def test_sorted_worst_first_and_top_n():
    rows = [_row(f"o{i}", 0.3 + i / 10, "irrelevant") for i in range(1, 5)]
    res = find_disagreements(rows, threshold=0.3, top_n=2)
    # Biggest gap (o4: 0.7-0.3=0.4) first, capped to top_n.
    assert [e["item_id"] for e in res["over_scored"]] == ["o4", "o3"]


def test_null_score_treated_as_zero():
    rows = [_row("n", None, "relevant")]
    res = find_disagreements(rows, threshold=0.3)
    assert [e["item_id"] for e in res["under_scored"]] == ["n"]
    assert res["under_scored"][0]["gap"] == 0.3


# --- Storage integration ---


def _store_story(temp_db, item_id, score, verdict):
    item = NormalizedItem(
        item_id=item_id,
        source_name="Test Feed",
        source_type="rss",
        source_priority=3,
        source_tags=["test"],
        title=f"Story {item_id}",
        link=f"https://example.com/{item_id}",
        published_date=datetime(2024, 1, 1, 12, 0),
        summary="",
    )
    temp_db.upsert_story(
        ScoredItem(item=item, relevance_score=score, matched_categories=["x"]),
        primary_category="x",
    )
    temp_db.add_feedback(item_id, verdict, channel="cli")


def test_storage_pairs_score_with_latest_owner_verdict(temp_db):
    _store_story(temp_db, "hi", 0.9, "irrelevant")
    _store_story(temp_db, "lo", 0.1, "relevant")
    rows = temp_db.get_scored_owner_feedback()
    by_id = {r["item_id"]: r for r in rows}
    assert by_id["hi"]["score"] == 0.9
    assert by_id["hi"]["verdict"] == "irrelevant"
    res = find_disagreements(rows, threshold=0.3)
    assert {e["item_id"] for e in res["over_scored"]} == {"hi"}
    assert {e["item_id"] for e in res["under_scored"]} == {"lo"}


def test_storage_latest_verdict_wins(temp_db):
    _store_story(temp_db, "x", 0.9, "irrelevant")
    # Owner changes their mind: now relevant. Latest should win → no over-score.
    temp_db.add_feedback("x", "relevant", channel="cli")
    rows = temp_db.get_scored_owner_feedback()
    assert rows[0]["verdict"] == "relevant"
    res = find_disagreements(rows, threshold=0.3)
    assert res["over_scored"] == []


def test_storage_excludes_wrong_category_and_crowd(temp_db):
    _store_story(temp_db, "wc", 0.9, "wrong_category")
    # Crowd (non-owner) channel must not count as ground truth.
    item = NormalizedItem(
        item_id="crowd",
        source_name="Test Feed",
        source_type="rss",
        source_priority=3,
        source_tags=["test"],
        title="Story crowd",
        link="https://example.com/crowd",
        published_date=datetime(2024, 1, 1, 12, 0),
        summary="",
    )
    temp_db.upsert_story(
        ScoredItem(item=item, relevance_score=0.9, matched_categories=["x"]),
        primary_category="x",
    )
    temp_db.add_feedback("crowd", "irrelevant", channel="github")
    rows = temp_db.get_scored_owner_feedback()
    assert rows == []
