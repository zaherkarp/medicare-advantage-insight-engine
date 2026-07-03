"""Tests for deduplication behavior."""

from datetime import datetime

from ma_signal_monitor.dedupe import (
    filter_new_items,
    mark_items_seen,
    suppress_duplicate_alerts,
)
from ma_signal_monitor.drafting import draft_alert
from ma_signal_monitor.models import DeliveryResult, NormalizedItem, ScoredItem


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


def _alert(config, title, *, source="Test Feed", score=0.6):
    """Build a real Alert (via draft_alert) with a given title and score."""
    item = NormalizedItem(
        item_id=f"{source}:{title}",
        source_name=source,
        source_type="rss",
        source_priority=4,
        source_tags=[],
        title=title,
        link=f"https://example.com/{abs(hash((source, title)))}",
        published_date=datetime(2024, 1, 1, 12, 0),
        summary="",
    )
    scored = ScoredItem(
        item=item, relevance_score=score, matched_categories=["policy_regulatory"]
    )
    return draft_alert(scored, config)


class TestAlertSuppression:
    """Near-duplicate alert suppression (within-run + cross-run)."""

    def test_within_run_keeps_highest_scored_of_cluster(self, sample_config, temp_db):
        # Same story from two sources; alerts arrive score-descending.
        a = _alert(
            sample_config,
            "UnitedHealth, FTC reach insulin settlement",
            source="Healthcare Dive",
            score=0.72,
        )
        b = _alert(
            sample_config,
            "UnitedHealth and FTC reach a proposed insulin settlement",
            source="Becker's",
            score=0.55,
        )
        distinct = _alert(
            sample_config,
            "CMS finalizes 2027 Star Ratings methodology",
            source="KFF",
            score=0.61,
        )

        kept, suppressed = suppress_duplicate_alerts(
            [a, b, distinct], temp_db, sample_config
        )

        assert suppressed == 1
        titles = [k.internal.title for k in kept]
        assert "insulin settlement" in titles[0]  # the higher-scored representative
        assert any("Star Ratings" in t for t in titles)
        assert len(kept) == 2

    def test_cross_run_suppresses_recently_delivered(self, sample_config, temp_db):
        # A near-duplicate was already delivered in a prior run.
        temp_db.log_delivery(
            DeliveryResult(
                alert_title="UnitedHealth, FTC reach insulin settlement",
                success=True,
                timestamp=datetime.utcnow(),
            )
        )
        again = _alert(
            sample_config, "UnitedHealth and FTC reach a proposed insulin settlement"
        )

        kept, suppressed = suppress_duplicate_alerts([again], temp_db, sample_config)

        assert suppressed == 1
        assert kept == []

    def test_cross_run_ignores_failed_deliveries(self, sample_config, temp_db):
        # A failed delivery is not "already alerted" — the story should still fire.
        temp_db.log_delivery(
            DeliveryResult(
                alert_title="UnitedHealth, FTC reach insulin settlement",
                success=False,
                timestamp=datetime.utcnow(),
            )
        )
        again = _alert(sample_config, "UnitedHealth, FTC reach insulin settlement")

        kept, suppressed = suppress_duplicate_alerts([again], temp_db, sample_config)

        assert suppressed == 0
        assert len(kept) == 1

    def test_disabled_is_passthrough(self, sample_config, temp_db):
        sample_config.dedup_enabled = False
        a = _alert(
            sample_config, "UnitedHealth, FTC reach insulin settlement", source="A"
        )
        b = _alert(
            sample_config,
            "UnitedHealth and FTC reach a proposed insulin settlement",
            source="B",
        )

        kept, suppressed = suppress_duplicate_alerts([a, b], temp_db, sample_config)

        assert suppressed == 0
        assert len(kept) == 2

    def test_lookback_zero_skips_cross_run(self, sample_config, temp_db):
        sample_config.dedup_lookback_days = 0
        temp_db.log_delivery(
            DeliveryResult(
                alert_title="Humana cuts 2026 guidance",
                success=True,
                timestamp=datetime.utcnow(),
            )
        )
        again = _alert(sample_config, "Humana cuts 2026 guidance")

        kept, suppressed = suppress_duplicate_alerts([again], temp_db, sample_config)

        assert suppressed == 0  # cross-run check disabled
        assert len(kept) == 1

    def test_empty_input_is_noop(self, sample_config, temp_db):
        assert suppress_duplicate_alerts([], temp_db, sample_config) == ([], 0)
