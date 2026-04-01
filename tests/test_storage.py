"""Tests for state storage and persistence."""

from ma_signal_monitor.models import DeliveryResult


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

        seen_deleted, _ = temp_db.cleanup_old_records(seen_retention_days=90)
        assert seen_deleted == 1
        assert not temp_db.is_seen("old_item")
        assert temp_db.is_seen("new_item")

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
