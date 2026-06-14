"""Tests for state storage and persistence."""

from datetime import datetime

from ma_signal_monitor.models import DeliveryResult, NormalizedItem, ScoredItem


def _make_scored(
    item_id: str,
    title: str,
    *,
    published: datetime | None,
    score: float = 0.5,
    categories: list[str] | None = None,
    entities: list[str] | None = None,
    summary: str = "A summary",
) -> ScoredItem:
    """Build a ScoredItem for story-archive tests."""
    item = NormalizedItem(
        item_id=item_id,
        source_name="Test Feed",
        source_type="rss",
        source_priority=4,
        source_tags=["test"],
        title=title,
        link=f"https://example.com/{item_id}",
        published_date=published,
        summary=summary,
    )
    return ScoredItem(
        item=item,
        relevance_score=score,
        matched_categories=categories or [],
        matched_entities=entities or [],
    )


class TestStateStore:
    """Test SQLite state store operations."""

    def test_mark_and_check_seen(self, temp_db):
        """Items can be marked and checked as seen."""
        assert not temp_db.is_seen("item_001")
        temp_db.mark_seen("item_001", "Feed A", "Title", "https://x.com/1")
        assert temp_db.is_seen("item_001")

    def test_seen_count(self, temp_db):
        """Seen count increments correctly."""
        assert temp_db.get_seen_count() == 0
        temp_db.mark_seen("a", "Feed", "T1", "https://x.com/1")
        temp_db.mark_seen("b", "Feed", "T2", "https://x.com/2")
        assert temp_db.get_seen_count() == 2

    def test_mark_seen_idempotent(self, temp_db):
        """Marking the same item twice doesn't duplicate."""
        temp_db.mark_seen("item_001", "Feed A", "Title", "https://x.com/1")
        temp_db.mark_seen("item_001", "Feed A", "Title", "https://x.com/1")
        assert temp_db.get_seen_count() == 1

    def test_delivery_log(self, temp_db):
        """Delivery results are logged."""
        result = DeliveryResult(
            alert_title="Test Alert",
            success=True,
            status_code=200,
        )
        temp_db.log_delivery(result)

        conn = temp_db._get_conn()
        row = conn.execute("SELECT * FROM delivery_log").fetchone()
        assert row["alert_title"] == "Test Alert"
        assert row["success"] == 1

    def test_run_metadata(self, temp_db):
        """Run start/end metadata is tracked."""
        run_id = temp_db.start_run()
        assert run_id is not None
        temp_db.end_run(run_id, items_fetched=10, items_new=5, alerts_sent=3)

        conn = temp_db._get_conn()
        row = conn.execute(
            "SELECT * FROM run_metadata WHERE id = ?", (run_id,)
        ).fetchone()
        assert row["items_fetched"] == 10
        assert row["items_new"] == 5
        assert row["alerts_sent"] == 3
        assert row["run_end"] is not None

    def test_cleanup_old_records(self, temp_db):
        """Cleanup removes old records based on retention."""
        # Mark an item seen
        temp_db.mark_seen("old_item", "Feed", "Old", "https://x.com/old")
        # Force old timestamp
        conn = temp_db._get_conn()
        conn.execute(
            "UPDATE seen_items SET first_seen_at = '2020-01-01T00:00:00' WHERE item_id = 'old_item'"
        )
        conn.commit()

        temp_db.mark_seen("new_item", "Feed", "New", "https://x.com/new")

        seen_deleted, _, _ = temp_db.cleanup_old_records(seen_retention_days=90)
        assert seen_deleted == 1
        assert not temp_db.is_seen("old_item")
        assert temp_db.is_seen("new_item")

    def test_upsert_story_roundtrip(self, temp_db):
        """A scored item is persisted with all fields intact."""
        scored = _make_scored(
            "s1",
            "UnitedHealthcare expands in Texas",
            published=datetime(2024, 1, 5, 9, 0),
            score=0.72,
            categories=["membership_movement", "competitive_strategy"],
            entities=["UnitedHealthcare"],
        )
        temp_db.upsert_story(
            scored,
            primary_category="membership_movement",
            public_draft={"opening_hook": "hook"},
            states=["TX"],
        )
        row = temp_db.get_story("s1")
        assert row is not None
        assert row["title"] == "UnitedHealthcare expands in Texas"
        assert row["primary_category"] == "membership_movement"
        assert row["relevance_score"] == 0.72
        view = temp_db.get_stories()[0]
        import json

        assert json.loads(view["entities"]) == ["UnitedHealthcare"]
        assert json.loads(view["states"]) == ["TX"]
        assert json.loads(view["public_draft"]) == {"opening_hook": "hook"}

    def test_upsert_story_is_idempotent(self, temp_db):
        """Re-persisting the same item id replaces, not duplicates."""
        scored = _make_scored("dup", "Title", published=datetime(2024, 1, 1))
        temp_db.upsert_story(scored, primary_category="policy_regulatory")
        scored.relevance_score = 0.9
        temp_db.upsert_story(scored, primary_category="policy_regulatory")
        assert temp_db.count_stories() == 1
        assert temp_db.get_story("dup")["relevance_score"] == 0.9

    def test_get_stories_reverse_chronological(self, temp_db):
        """Stories are returned newest-first by published date."""
        temp_db.upsert_story(
            _make_scored("old", "Old", published=datetime(2024, 1, 1)),
            primary_category="policy_regulatory",
        )
        temp_db.upsert_story(
            _make_scored("new", "New", published=datetime(2024, 6, 1)),
            primary_category="policy_regulatory",
        )
        order = [r["item_id"] for r in temp_db.get_stories()]
        assert order == ["new", "old"]

    def test_dateless_story_sorts_by_fetched_at(self, temp_db):
        """Items without a published date still appear (sorted by fetched_at)."""
        temp_db.upsert_story(
            _make_scored("nodate", "No date", published=None),
            primary_category="policy_regulatory",
        )
        rows = temp_db.get_stories()
        assert len(rows) == 1
        assert rows[0]["item_id"] == "nodate"

    def test_category_and_pagination_filters(self, temp_db):
        """Category filter and limit/offset pagination work."""
        for i in range(5):
            temp_db.upsert_story(
                _make_scored(
                    f"p{i}", f"Policy {i}", published=datetime(2024, 1, i + 1)
                ),
                primary_category="policy_regulatory",
            )
        temp_db.upsert_story(
            _make_scored("fin", "Finance", published=datetime(2024, 2, 1)),
            primary_category="financial_pressure",
        )
        assert temp_db.count_stories(category="policy_regulatory") == 5
        assert temp_db.count_stories(category="financial_pressure") == 1
        page1 = temp_db.get_stories(category="policy_regulatory", limit=2, offset=0)
        page2 = temp_db.get_stories(category="policy_regulatory", limit=2, offset=2)
        assert len(page1) == 2 and len(page2) == 2
        assert {r["item_id"] for r in page1} != {r["item_id"] for r in page2}

    def test_state_filter_and_counts(self, temp_db):
        """State filtering and aggregate counts use the JSON states field."""
        temp_db.upsert_story(
            _make_scored("tx", "Texas news", published=datetime(2024, 1, 1)),
            primary_category="membership_movement",
            states=["TX"],
        )
        temp_db.upsert_story(
            _make_scored("txca", "Texas & California", published=datetime(2024, 1, 2)),
            primary_category="membership_movement",
            states=["TX", "CA"],
        )
        assert temp_db.count_stories(state="TX") == 2
        assert temp_db.count_stories(state="CA") == 1
        counts = temp_db.get_state_counts()
        assert counts["TX"] == 2
        assert counts["CA"] == 1

    def test_category_and_source_counts(self, temp_db):
        """Aggregate counts for the status dashboard."""
        temp_db.upsert_story(
            _make_scored("a", "A", published=None),
            primary_category="policy_regulatory",
        )
        temp_db.upsert_story(
            _make_scored("b", "B", published=None),
            primary_category="policy_regulatory",
        )
        temp_db.upsert_story(
            _make_scored("c", "C", published=None),
            primary_category="financial_pressure",
        )
        cats = temp_db.get_category_counts()
        assert cats["policy_regulatory"] == 2
        assert cats["financial_pressure"] == 1
        # All three share source_name "Test Feed" (from _make_scored).
        assert temp_db.get_source_counts()["Test Feed"] == 3

    def test_get_last_run(self, temp_db):
        """get_last_run returns the most recent completed run."""
        assert temp_db.get_last_run() is None
        rid = temp_db.start_run()
        temp_db.end_run(rid, items_fetched=5, items_new=3)
        last = temp_db.get_last_run()
        assert last is not None
        assert last["items_fetched"] == 5

    def test_existing_html_titles_cleaned_on_open(self, tmp_path):
        """Reopening the store strips HTML from already-stored titles + FTS."""
        from ma_signal_monitor.storage import StateStore

        db = tmp_path / "heal.db"
        store = StateStore(db)
        store.upsert_story(
            _make_scored(
                "dirty",
                '<a href="/x" hreflang="en">Feds overpaid MA plans</a>',
                published=None,
            ),
            primary_category="policy_regulatory",
        )
        store.close()

        # Reopening triggers the one-time cleanup.
        store2 = StateStore(db)
        try:
            assert store2.get_story("dirty")["title"] == "Feds overpaid MA plans"
            # FTS was updated too (searchable by the clean text, tag gone).
            hits = store2.search_stories("overpaid")
            assert len(hits) == 1 and "<a" not in hits[0]["title"]
        finally:
            store2.close()

    def test_db_persists_across_reconnect(self, tmp_path):
        """Data persists after closing and reopening the store."""
        from ma_signal_monitor.storage import StateStore

        db_path = tmp_path / "persist_test.db"
        store1 = StateStore(db_path)
        store1.mark_seen("persist_item", "Feed", "Title", "https://x.com")
        store1.close()

        store2 = StateStore(db_path)
        assert store2.is_seen("persist_item")
        store2.close()


class TestFeedback:
    """Reader-feedback storage."""

    def test_add_and_summarize(self, temp_db):
        temp_db.add_feedback("item-1", "relevant")
        temp_db.add_feedback("item-1", "irrelevant")  # later owner vote wins
        summary = temp_db.get_feedback_summary("item-1")
        assert summary["my_verdict"] == "irrelevant"
        assert summary["counts"] == {"relevant": 1, "irrelevant": 1}
        assert temp_db.count_feedback() == 2

    def test_owner_vs_crowd_weight(self, temp_db):
        temp_db.add_feedback("item-1", "relevant", channel="local_web")
        temp_db.add_feedback(
            "item-1", "relevant", channel="github", voter_key="octocat"
        )
        conn = temp_db._get_conn()
        weights = {
            r["channel"]: r["weight"]
            for r in conn.execute(
                "SELECT channel, weight FROM feedback WHERE item_id = 'item-1'"
            )
        }
        assert weights["local_web"] == 1.0
        assert weights["github"] < 1.0

    def test_crowd_ingest_is_idempotent(self, temp_db):
        # Same (channel, source_ref) ingested twice → one row.
        for _ in range(2):
            temp_db.add_feedback(
                "item-1",
                "relevant",
                channel="github",
                voter_key="octocat",
                source_ref="discussion:1#reaction:9",
            )
        assert temp_db.count_feedback() == 1

    def test_my_verdict_ignores_crowd(self, temp_db):
        temp_db.add_feedback("item-1", "relevant", channel="local_web")
        temp_db.add_feedback(
            "item-1", "irrelevant", channel="github", voter_key="octocat"
        )
        # Crowd vote does not override the owner's verdict readout.
        assert temp_db.get_feedback_summary("item-1")["my_verdict"] == "relevant"

    def test_invalid_verdict_rejected(self, temp_db):
        import pytest

        with pytest.raises(ValueError):
            temp_db.add_feedback("item-1", "bogus")
