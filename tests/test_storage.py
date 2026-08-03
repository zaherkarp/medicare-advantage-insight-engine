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

    def test_recent_top_stories_until_bounds_window(self, temp_db):
        """``until`` (exclusive) carves adjacent windows for momentum compares."""
        temp_db.upsert_story(
            _make_scored("before", "Too old", published=datetime(2024, 1, 1)),
            primary_category="policy_regulatory",
        )
        temp_db.upsert_story(
            _make_scored("inside", "In window", published=datetime(2024, 1, 10)),
            primary_category="policy_regulatory",
        )
        temp_db.upsert_story(
            _make_scored("at-until", "Right edge", published=datetime(2024, 1, 20)),
            primary_category="policy_regulatory",
        )
        # A near-duplicate and a sub-floor story inside the window stay hidden.
        temp_db.upsert_story(
            _make_scored("dup", "In window too", published=datetime(2024, 1, 11)),
            primary_category="policy_regulatory",
            duplicate_of="inside",
        )
        temp_db.upsert_story(
            _make_scored(
                "noise", "Sub-floor", published=datetime(2024, 1, 12), score=0.02
            ),
            primary_category="policy_regulatory",
        )

        rows = temp_db.get_recent_top_stories(
            datetime(2024, 1, 5), min_score=0.1, until=datetime(2024, 1, 20)
        )
        # `since` inclusive, `until` exclusive, duplicates + noise excluded.
        assert [r["item_id"] for r in rows] == ["inside"]

        open_ended = temp_db.get_recent_top_stories(
            datetime(2024, 1, 10), min_score=0.1
        )
        assert {r["item_id"] for r in open_ended} == {"inside", "at-until"}

    def test_recent_story_facets_windows_and_dedupes(self, temp_db):
        """Uncapped facet fetch: windowed, deduped, floor-filtered, score-ordered."""
        temp_db.upsert_story(
            _make_scored(
                "p-old", "Before window", published=datetime(2024, 1, 1), score=0.9
            ),
            primary_category="policy_regulatory",
        )
        temp_db.upsert_story(
            _make_scored(
                "p-1", "In window A", published=datetime(2024, 1, 10), score=0.4
            ),
            primary_category="policy_regulatory",
        )
        temp_db.upsert_story(
            _make_scored(
                "f-1", "In window B", published=datetime(2024, 1, 12), score=0.8
            ),
            primary_category="financial_pressure",
        )
        # A near-duplicate and a sub-floor story inside the window are excluded.
        temp_db.upsert_story(
            _make_scored("p-dup", "Dup of p-1", published=datetime(2024, 1, 13)),
            primary_category="policy_regulatory",
            duplicate_of="p-1",
        )
        temp_db.upsert_story(
            _make_scored(
                "p-noise", "Sub-floor", published=datetime(2024, 1, 14), score=0.02
            ),
            primary_category="policy_regulatory",
        )
        # A story at the right edge is excluded (`until` is exclusive).
        temp_db.upsert_story(
            _make_scored("edge", "At until", published=datetime(2024, 1, 20)),
            primary_category="policy_regulatory",
        )

        rows = temp_db.get_recent_story_facets(
            datetime(2024, 1, 5), min_score=0.1, until=datetime(2024, 1, 20)
        )
        # `since` inclusive, `until` exclusive; dup + noise gone; score-ordered.
        assert [r["item_id"] for r in rows] == ["f-1", "p-1"]

        # No LIMIT, and the left bound is inclusive.
        open_ended = temp_db.get_recent_story_facets(
            datetime(2024, 1, 10), min_score=0.1
        )
        assert {r["item_id"] for r in open_ended} == {"f-1", "p-1", "edge"}

    def test_recent_story_facets_lean_column_set(self, temp_db):
        """The facet query selects exactly the lens columns `_facet_view` reads.

        Guards the lean view against drift: adding/removing a column here would
        break the web layer's ``_facet_view`` (which references these by name).
        """
        import json

        temp_db.upsert_story(
            _make_scored(
                "f1",
                "Facet story",
                published=datetime(2024, 1, 10),
                categories=["policy_regulatory", "financial_pressure"],
                entities=["Humana"],
            ),
            primary_category="policy_regulatory",
            states=["FL"],
        )
        rows = temp_db.get_recent_story_facets(datetime(2024, 1, 1), min_score=0.1)
        assert len(rows) == 1
        assert set(rows[0].keys()) == {
            "item_id",
            "title",
            "link",
            "source_name",
            "published_date",
            "fetched_at",
            "relevance_score",
            "primary_category",
            "categories",
            "entities",
            "states",
        }
        # The heavy blobs the lean query deliberately skips.
        assert "summary" not in rows[0].keys()
        assert "public_draft" not in rows[0].keys()
        # JSON lenses round-trip for the intersection engine.
        assert json.loads(rows[0]["categories"]) == [
            "policy_regulatory",
            "financial_pressure",
        ]
        assert json.loads(rows[0]["entities"]) == ["Humana"]
        assert json.loads(rows[0]["states"]) == ["FL"]

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

    def test_min_score_filters_browsable_stories(self, temp_db):
        """The archive keeps everything; min_score gates the browsable views."""
        temp_db.upsert_story(
            _make_scored(
                "noise",
                "Pure priority noise",
                published=datetime(2024, 1, 1),
                score=0.04,
            ),
            primary_category="uncategorized",
        )
        temp_db.upsert_story(
            _make_scored(
                "signal",
                "Real MA signal",
                published=datetime(2024, 1, 2),
                score=0.4,
                categories=["policy_regulatory"],
            ),
            primary_category="policy_regulatory",
        )
        # Default (min_score=0.0) surfaces the full archive — unchanged behavior.
        assert temp_db.count_stories() == 2
        assert len(temp_db.get_stories()) == 2
        # Floored views drop the sub-floor item only.
        assert temp_db.count_stories(min_score=0.1) == 1
        assert [r["item_id"] for r in temp_db.get_stories(min_score=0.1)] == ["signal"]

    def test_min_score_boundary_is_inclusive(self, temp_db):
        """A story scoring exactly at the floor is kept (>=), just below is not."""
        temp_db.upsert_story(
            _make_scored("edge", "Edge", published=datetime(2024, 1, 1), score=0.1),
            primary_category="policy_regulatory",
        )
        assert temp_db.count_stories(min_score=0.1) == 1
        assert temp_db.count_stories(min_score=0.11) == 0

    def test_since_windows_stories(self, temp_db):
        """``since`` bounds the browsable window on the left (inclusive)."""
        temp_db.upsert_story(
            _make_scored("old", "Old", published=datetime(2024, 1, 1, 9, 0)),
            primary_category="policy_regulatory",
        )
        temp_db.upsert_story(
            _make_scored(
                "new",
                "New",
                published=datetime(2024, 6, 1),
                entities=["Humana"],
            ),
            primary_category="membership_movement",
        )
        since = datetime(2024, 6, 1).isoformat()
        assert [r["item_id"] for r in temp_db.get_stories(since=since)] == ["new"]
        assert temp_db.count_stories(since=since) == 1
        # The boundary is inclusive; a moment later excludes it.
        assert temp_db.count_stories(since="2024-06-01T00:00:01") == 0
        # Composes with the other filters.
        assert temp_db.count_stories(since=since, entity_aliases=["Humana"]) == 1
        assert temp_db.count_stories(since=since, category="policy_regulatory") == 0

    def test_since_dateless_story_falls_back_to_fetched_at(self, temp_db):
        """A dateless story windows on fetched_at (stamped now at upsert)."""
        temp_db.upsert_story(
            _make_scored("nodate", "No date", published=None),
            primary_category="policy_regulatory",
        )
        assert temp_db.count_stories(since="2000-01-01T00:00:00") == 1
        assert temp_db.count_stories(since="2999-01-01T00:00:00") == 0

    def test_min_score_filters_state_counts(self, temp_db):
        """State tallies honor the floor so they match the filtered state feed."""
        temp_db.upsert_story(
            _make_scored("n", "noise", published=datetime(2024, 1, 1), score=0.04),
            primary_category="uncategorized",
            states=["TX"],
        )
        temp_db.upsert_story(
            _make_scored(
                "s",
                "signal",
                published=datetime(2024, 1, 2),
                score=0.5,
                categories=["membership_movement"],
            ),
            primary_category="membership_movement",
            states=["TX"],
        )
        assert temp_db.get_state_counts()["TX"] == 2
        assert temp_db.get_state_counts(min_score=0.1)["TX"] == 1

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

    def test_get_oldest_story_key_empty_db(self, temp_db):
        """An empty archive (or empty scope) resolves to None, not an error."""
        assert temp_db.get_oldest_story_key() is None
        assert temp_db.get_oldest_story_key(category="policy_regulatory") is None

    def test_get_oldest_story_key_returns_oldest_across_fallback(self, temp_db):
        """Oldest sort key, falling back to fetched_at like get_stories orders."""
        temp_db.upsert_story(
            _make_scored("mid", "Middle", published=datetime(2024, 3, 1)),
            primary_category="policy_regulatory",
        )
        temp_db.upsert_story(
            _make_scored("newest", "Newest", published=datetime(2024, 6, 1)),
            primary_category="policy_regulatory",
        )
        # A dateless story falls back to fetched_at (stamped ~now at upsert),
        # so it's newer than either published-dated story above.
        temp_db.upsert_story(
            _make_scored("nodate", "No date", published=None),
            primary_category="policy_regulatory",
        )
        assert temp_db.get_oldest_story_key() == "2024-03-01T00:00:00"

    def test_get_oldest_story_key_respects_category_filter(self, temp_db):
        """Scoping filters narrow the oldest-key search the same as get_stories."""
        temp_db.upsert_story(
            _make_scored("old-fin", "Old finance", published=datetime(2024, 1, 1)),
            primary_category="financial_pressure",
        )
        temp_db.upsert_story(
            _make_scored("newer-pol", "Newer policy", published=datetime(2024, 5, 1)),
            primary_category="policy_regulatory",
        )
        assert temp_db.get_oldest_story_key() == "2024-01-01T00:00:00"
        assert (
            temp_db.get_oldest_story_key(category="policy_regulatory")
            == "2024-05-01T00:00:00"
        )
        assert temp_db.get_oldest_story_key(category="competitive_strategy") is None

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


class TestAlertFeedback:
    """Alert-outcome feedback: scoring breakdown, delivery linkage, verdicts."""

    def test_upsert_story_persists_breakdown_and_threshold(self, temp_db):
        import json

        from ma_signal_monitor.models import ScoringReason

        scored = _make_scored(
            "s1", "CMS Star Ratings update", published=datetime(2024, 1, 5), score=0.55
        )
        scored.reasons = [
            ScoringReason("title_keyword", "'star rating' in title", 0.225),
            ScoringReason("source_priority", "Source priority 4/5", 0.08),
        ]
        temp_db.upsert_story(
            scored, primary_category="policy_regulatory", threshold_at_score=0.3
        )

        row = temp_db.get_story("s1")
        breakdown = json.loads(row["scoring_breakdown"])
        assert breakdown == [
            {
                "factor": "title_keyword",
                "detail": "'star rating' in title",
                "contribution": 0.225,
            },
            {
                "factor": "source_priority",
                "detail": "Source priority 4/5",
                "contribution": 0.08,
            },
        ]
        assert row["threshold_at_score"] == 0.3

    def test_delivery_log_stores_item_id(self, temp_db):
        result = DeliveryResult(
            alert_title="Test Alert", success=True, status_code=200, item_id="s1"
        )
        temp_db.log_delivery(result)
        conn = temp_db._get_conn()
        row = conn.execute("SELECT item_id FROM delivery_log").fetchone()
        assert row["item_id"] == "s1"

    def test_get_alert_delivered(self, temp_db):
        assert temp_db.get_alert_delivered("s1") is False
        temp_db.log_delivery(
            DeliveryResult(
                alert_title="T", success=False, status_code=500, item_id="s1"
            )
        )
        # A failed delivery attempt doesn't count as "posted".
        assert temp_db.get_alert_delivered("s1") is False
        temp_db.log_delivery(
            DeliveryResult(alert_title="T", success=True, status_code=200, item_id="s1")
        )
        assert temp_db.get_alert_delivered("s1") is True

    def test_get_alert_feedback_unknown_story_returns_none(self, temp_db):
        assert temp_db.get_alert_feedback("missing") is None

    def test_get_alert_feedback_joins_score_and_verdicts(self, temp_db):
        scored = _make_scored("s1", "Title", published=datetime(2024, 1, 5), score=0.42)
        temp_db.upsert_story(
            scored, primary_category="policy_regulatory", threshold_at_score=0.3
        )
        temp_db.log_delivery(
            DeliveryResult(alert_title="T", success=True, status_code=200, item_id="s1")
        )
        temp_db.add_feedback("s1", "alert_correct", channel="cli")

        info = temp_db.get_alert_feedback("s1")
        assert info["relevance_score"] == 0.42
        assert info["threshold_at_score"] == 0.3
        assert info["delivered"] is True
        assert [v["verdict"] for v in info["alert_verdicts"]] == ["alert_correct"]

    def test_alert_verdicts_accepted(self, temp_db):
        for verdict in ("alert_correct", "alert_false_positive", "alert_missed"):
            temp_db.add_feedback("s1", verdict, channel="cli")
        assert temp_db.count_feedback() == 3


class TestSourceYield:
    """Per-source relevance-yield stats."""

    def _store(self, temp_db, source, item_id, score):
        item = NormalizedItem(
            item_id=item_id,
            source_name=source,
            source_type="rss",
            source_priority=3,
            source_tags=["test"],
            title=f"Story {item_id}",
            link=f"https://example.com/{item_id}",
            published_date=datetime(2024, 1, 1, 12, 0),
            summary="summary",
        )
        temp_db.upsert_story(
            ScoredItem(item=item, relevance_score=score, matched_categories=["x"]),
            primary_category="x",
        )

    def test_yield_computed_and_sorted(self, temp_db):
        # Good Feed: 2/2 above 0.3; Junk Feed: 0/2 above 0.3.
        self._store(temp_db, "Good Feed", "g1", 0.7)
        self._store(temp_db, "Good Feed", "g2", 0.5)
        self._store(temp_db, "Junk Feed", "j1", 0.05)
        self._store(temp_db, "Junk Feed", "j2", 0.1)

        stats = temp_db.get_source_yield(min_score=0.3)
        by_source = {s["source"]: s for s in stats}
        assert by_source["Good Feed"]["yield"] == 1.0
        assert by_source["Junk Feed"]["yield"] == 0.0
        assert by_source["Junk Feed"]["max_score"] == 0.1
        # Worst yield sorts first.
        assert stats[0]["source"] == "Junk Feed"
