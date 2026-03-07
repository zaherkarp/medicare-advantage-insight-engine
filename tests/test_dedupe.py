"""Tests for deduplication behavior."""

from ma_signal_monitor.dedupe import filter_new_items, mark_items_seen


class TestDeduplication:
    """Test dedup filtering against the state store."""

    def test_all_new_items_pass(self, sample_normalized_items, temp_db):
        """All items pass when none have been seen."""
        new = filter_new_items(sample_normalized_items, temp_db)
        assert len(new) == len(sample_normalized_items)

    def test_seen_items_filtered(self, sample_normalized_items, temp_db):
        """Previously seen items are filtered out."""
        # Mark first item as seen
        first = sample_normalized_items[0]
        temp_db.mark_seen(first.item_id, first.source_name, first.title, first.link)

        new = filter_new_items(sample_normalized_items, temp_db)
        assert len(new) == len(sample_normalized_items) - 1
        assert all(item.item_id != first.item_id for item in new)

    def test_all_seen_returns_empty(self, sample_normalized_items, temp_db):
        """When all items are seen, returns empty list."""
        mark_items_seen(sample_normalized_items, temp_db)
        new = filter_new_items(sample_normalized_items, temp_db)
        assert len(new) == 0

    def test_mark_seen_is_idempotent(self, sample_normalized_items, temp_db):
        """Marking the same item seen twice doesn't error."""
        mark_items_seen(sample_normalized_items, temp_db)
        mark_items_seen(sample_normalized_items, temp_db)  # Should not raise
        assert temp_db.get_seen_count() == len(sample_normalized_items)

    def test_empty_list_returns_empty(self, temp_db):
        """Empty input returns empty output."""
        assert filter_new_items([], temp_db) == []
