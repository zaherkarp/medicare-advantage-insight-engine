"""Tests for feed near-duplicate grouping: the duplicate_of migration,
detection at persist time, and the browsable-surface filtering."""

import sqlite3
from datetime import datetime, timedelta

from ma_signal_monitor.dedupe import assign_story_duplicates
from ma_signal_monitor.models import NormalizedItem, ScoredItem
from ma_signal_monitor.storage import StateStore


def _scored(item_id, title, *, source="Feed", score=0.6, published=None):
    item = NormalizedItem(
        item_id=item_id,
        source_name=source,
        source_type="rss",
        source_priority=4,
        source_tags=[],
        title=title,
        link=f"https://example.com/{item_id}",
        published_date=published or datetime(2024, 1, 1, 12, 0),
        summary="",
    )
    return ScoredItem(
        item=item, relevance_score=score, matched_categories=["policy_regulatory"]
    )


def _persist(store, scored, dup_of=None):
    store.upsert_story(
        scored, primary_category="policy_regulatory", duplicate_of=dup_of
    )


# --- Migration ---


def test_duplicate_of_column_added_to_old_db(tmp_path):
    """A pre-column stories table gains duplicate_of when the store reopens."""
    db = tmp_path / "old.db"
    # Build a stories table WITHOUT duplicate_of, by hand.
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE stories (
            item_id TEXT PRIMARY KEY, title TEXT NOT NULL, link TEXT NOT NULL,
            source_name TEXT NOT NULL, source_priority INTEGER, summary TEXT,
            published_date TEXT, fetched_at TEXT NOT NULL, relevance_score REAL,
            primary_category TEXT, categories TEXT, entities TEXT, states TEXT,
            public_draft TEXT)"""
    )
    conn.execute(
        "INSERT INTO stories (item_id, title, link, source_name, fetched_at) "
        "VALUES ('a', 'Old story', 'https://x', 'Feed', '2024-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    cols_before = {
        r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(stories)")
    }
    assert "duplicate_of" not in cols_before

    store = StateStore(db)  # _init_db → _migrate adds the column
    try:
        cols = {r[1] for r in store._get_conn().execute("PRAGMA table_info(stories)")}
        assert "duplicate_of" in cols
        # Existing row is preserved and defaults to NULL (a representative).
        row = store.get_story("a")
        assert row["title"] == "Old story" and row["duplicate_of"] is None
    finally:
        store.close()

    # Reopening is a no-op (idempotent) — must not raise.
    StateStore(db).close()


def test_ensure_column_is_idempotent(temp_db):
    added_first = temp_db._ensure_column("stories", "duplicate_of", "TEXT")
    assert added_first is False  # already present from the schema


# --- Storage queries ---


def test_recent_story_reps_excludes_duplicates_and_old(temp_db):
    now = datetime.utcnow()
    _persist(temp_db, _scored("rep", "A representative story", published=now))
    _persist(
        temp_db,
        _scored("dup", "A representative story copy", published=now),
        dup_of="rep",
    )
    _persist(
        temp_db,
        _scored("old", "An old representative", published=now - timedelta(days=30)),
    )
    reps = dict(temp_db.recent_story_reps(since_days=3))
    assert "rep" in reps  # a recent representative
    assert "dup" not in reps  # duplicates are excluded
    assert "old" not in reps  # outside the lookback
    assert temp_db.recent_story_reps(since_days=0) == []  # disabled lookback


def test_get_duplicates_returns_the_group(temp_db):
    _persist(temp_db, _scored("rep", "Story", source="Dive"))
    _persist(temp_db, _scored("d1", "Story copy one", source="Beckers"), dup_of="rep")
    _persist(temp_db, _scored("d2", "Story copy two", source="Modern"), dup_of="rep")
    dups = temp_db.get_duplicates("rep")
    assert {d["source_name"] for d in dups} == {"Beckers", "Modern"}
    assert temp_db.get_duplicates("d1") == []  # a duplicate has no children


# --- Detection ---


def test_within_run_marks_lower_scored_as_duplicate(sample_config, temp_db):
    items = [
        _scored(
            "hd",
            "UnitedHealth, FTC reach insulin settlement",
            source="Dive",
            score=0.72,
        ),
        _scored(
            "bk",
            "UnitedHealth and FTC reach a proposed insulin settlement",
            source="Beckers",
            score=0.55,
        ),
        _scored(
            "kf",
            "CMS finalizes 2027 Star Ratings methodology",
            source="KFF",
            score=0.61,
        ),
    ]
    dup = assign_story_duplicates(items, temp_db, sample_config)
    assert dup["hd"] is None  # highest-scored → representative
    assert dup["bk"] == "hd"  # near-dup points at the representative
    assert dup["kf"] is None  # distinct story


def test_cross_run_points_at_archived_root(sample_config, temp_db):
    now = datetime.utcnow()
    _persist(
        temp_db,
        _scored("hd", "UnitedHealth, FTC reach insulin settlement", published=now),
    )
    _persist(
        temp_db,
        _scored("bk", "UnitedHealth FTC insulin settlement reached", published=now),
        dup_of="hd",
    )
    later = [
        _scored(
            "mh", "UnitedHealth reaches insulin settlement with the FTC", score=0.64
        )
    ]
    dup = assign_story_duplicates(later, temp_db, sample_config)
    # Points at the ROOT representative, never at another duplicate.
    assert dup["mh"] == "hd"


def test_disabled_marks_nothing(sample_config, temp_db):
    sample_config.story_dedup_enabled = False
    items = [
        _scored("a", "UnitedHealth, FTC reach insulin settlement", score=0.7),
        _scored(
            "b", "UnitedHealth and FTC reach a proposed insulin settlement", score=0.5
        ),
    ]
    dup = assign_story_duplicates(items, temp_db, sample_config)
    assert dup == {"a": None, "b": None}
