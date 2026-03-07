"""Tests for feed item normalization."""

from datetime import datetime

from ma_signal_monitor.models import RawFeedItem
from ma_signal_monitor.normalize import normalize_item, normalize_items


class TestNormalizeItem:
    """Test individual item normalization."""

    def test_basic_normalization(self, sample_raw_items):
        """Item is normalized with correct fields."""
        raw = sample_raw_items[0]
        item = normalize_item(raw)
        assert item.title == raw.title
        assert item.link == raw.link
        assert item.source_name == raw.source_name
        assert item.item_id  # Has a generated ID
        assert len(item.item_id) == 16

    def test_date_parsing_rfc2822(self):
        """RFC 2822 dates are parsed correctly."""
        raw = RawFeedItem(
            source_name="Test", source_type="rss", source_url="https://x.com",
            source_priority=3, source_tags=[], title="Test",
            link="https://x.com/1", published="Mon, 01 Jan 2024 12:00:00 +0000",
            summary="Test summary",
        )
        item = normalize_item(raw)
        assert item.published_date is not None
        assert item.published_date.year == 2024

    def test_date_parsing_iso(self):
        """ISO format dates are parsed correctly."""
        raw = RawFeedItem(
            source_name="Test", source_type="rss", source_url="https://x.com",
            source_priority=3, source_tags=[], title="Test",
            link="https://x.com/1", published="2024-01-15T10:30:00Z",
            summary="Test summary",
        )
        item = normalize_item(raw)
        assert item.published_date is not None
        assert item.published_date.month == 1

    def test_unparseable_date_returns_none(self):
        """Unparseable dates result in None, not an error."""
        raw = RawFeedItem(
            source_name="Test", source_type="rss", source_url="https://x.com",
            source_priority=3, source_tags=[], title="Test",
            link="https://x.com/1", published="not a date",
            summary="Test summary",
        )
        item = normalize_item(raw)
        assert item.published_date is None

    def test_empty_date_returns_none(self):
        """Empty date string results in None."""
        raw = RawFeedItem(
            source_name="Test", source_type="rss", source_url="https://x.com",
            source_priority=3, source_tags=[], title="Test",
            link="https://x.com/1", published="", summary="Test",
        )
        item = normalize_item(raw)
        assert item.published_date is None

    def test_summary_truncation(self):
        """Long summaries are truncated."""
        raw = RawFeedItem(
            source_name="Test", source_type="rss", source_url="https://x.com",
            source_priority=3, source_tags=[], title="Test",
            link="https://x.com/1", published="", summary="x" * 1000,
        )
        item = normalize_item(raw, max_summary_length=100)
        assert len(item.summary) <= 100
        assert item.summary.endswith("...")

    def test_whitespace_cleaning(self):
        """Excess whitespace is collapsed."""
        raw = RawFeedItem(
            source_name="Test", source_type="rss", source_url="https://x.com",
            source_priority=3, source_tags=[], title="  Too   many   spaces  ",
            link="https://x.com/1", published="", summary="  Also  spaced  ",
        )
        item = normalize_item(raw)
        assert item.title == "Too many spaces"

    def test_stable_item_id_for_same_link(self):
        """Same source+link produces the same item_id."""
        raw = RawFeedItem(
            source_name="Feed A", source_type="rss", source_url="https://x.com",
            source_priority=3, source_tags=[], title="Title 1",
            link="https://x.com/article/1", published="", summary="",
        )
        id1 = normalize_item(raw).item_id
        raw.title = "Different title, same link"
        id2 = normalize_item(raw).item_id
        assert id1 == id2

    def test_different_links_different_ids(self):
        """Different links produce different item_ids."""
        raw1 = RawFeedItem(
            source_name="Feed A", source_type="rss", source_url="https://x.com",
            source_priority=3, source_tags=[], title="Test",
            link="https://x.com/1", published="", summary="",
        )
        raw2 = RawFeedItem(
            source_name="Feed A", source_type="rss", source_url="https://x.com",
            source_priority=3, source_tags=[], title="Test",
            link="https://x.com/2", published="", summary="",
        )
        assert normalize_item(raw1).item_id != normalize_item(raw2).item_id


class TestNormalizeItems:
    """Test batch normalization."""

    def test_normalizes_list(self, sample_raw_items):
        """All items in a list are normalized."""
        items = normalize_items(sample_raw_items)
        assert len(items) == len(sample_raw_items)

    def test_handles_empty_list(self):
        """Empty input returns empty output."""
        assert normalize_items([]) == []
