"""Tests for the reclassification backfill (ma_signal_monitor.backfill)."""

import json
from datetime import datetime

from ma_signal_monitor.backfill import backfill_categories, main
from ma_signal_monitor.models import NormalizedItem, ScoredItem


def _make_scored(
    item_id: str,
    title: str,
    *,
    published: datetime | None,
    score: float = 0.5,
    categories: list[str] | None = None,
    entities: list[str] | None = None,
    summary: str = "",
    source_priority: int = 4,
) -> ScoredItem:
    """Build a ScoredItem for seeding the story archive (mirrors test_storage.py)."""
    item = NormalizedItem(
        item_id=item_id,
        source_name="Test Feed",
        source_type="rss",
        source_priority=source_priority,
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


class TestBackfillCategories:
    """Core reclassification behavior."""

    def test_corrects_wrong_category(self, temp_db, sample_config):
        """A stale wrong category is corrected; score/entities/states untouched."""
        scored = _make_scored(
            "s1",
            "CMS proposes new Star Ratings methodology for Medicare Advantage",
            published=datetime(2024, 1, 1),
            score=0.42,
            categories=["financial_pressure"],  # stale — stored under old run
            entities=["Humana"],
        )
        temp_db.upsert_story(
            scored,
            primary_category="financial_pressure",
            states=["TX"],
        )

        report = backfill_categories(temp_db, sample_config)

        assert report["total"] == 1
        assert report["changed"] == 1
        assert report["unchanged"] == 0
        assert report["transitions"] == {("financial_pressure", "policy_regulatory"): 1}
        assert report["cat_to_uncategorized"] == 0
        assert report["uncategorized_to_cat"] == 0

        row = temp_db.get_story("s1")
        assert row["primary_category"] == "policy_regulatory"
        assert json.loads(row["categories"]) == ["policy_regulatory"]
        # Category-fields-only by default: scoring fields stay exactly as seeded.
        assert row["relevance_score"] == 0.42
        assert json.loads(row["entities"]) == ["Humana"]
        assert json.loads(row["states"]) == ["TX"]

        # FTS wasn't touched (categories aren't indexed) and still finds the row.
        hits = temp_db.search_stories("Star Ratings")
        assert any(h["item_id"] == "s1" for h in hits)

    def test_corrects_null_category(self, temp_db, sample_config):
        """A genuinely NULL primary_category is treated as 'uncategorized' and fixed."""
        scored = _make_scored(
            "s2",
            "UnitedHealthcare expands Medicare Advantage enrollment to new counties",
            published=datetime(2024, 1, 2),
            score=0.5,
            categories=[],
        )
        temp_db.upsert_story(scored, primary_category="uncategorized")
        conn = temp_db._get_conn()
        conn.execute(
            "UPDATE stories SET primary_category = NULL, categories = NULL "
            "WHERE item_id = ?",
            ("s2",),
        )
        conn.commit()

        report = backfill_categories(temp_db, sample_config)

        assert report["changed"] == 1
        assert report["uncategorized_to_cat"] == 1
        row = temp_db.get_story("s2")
        assert row["primary_category"] == "membership_movement"

    def test_unchanged_categorization_reports_zero_changes(
        self, temp_db, sample_config
    ):
        """A row already correctly classified is left alone."""
        scored = _make_scored(
            "s3",
            "Hospital opens new parking garage",
            published=datetime(2024, 1, 3),
            score=0.1,
            categories=[],
        )
        temp_db.upsert_story(scored, primary_category="uncategorized")

        report = backfill_categories(temp_db, sample_config)

        assert report["total"] == 1
        assert report["changed"] == 0
        assert report["unchanged"] == 1
        row = temp_db.get_story("s3")
        assert row["primary_category"] == "uncategorized"

    def test_rescore_updates_scoring_fields(self, temp_db, sample_config):
        """--rescore refreshes relevance_score/entities/states on changed rows."""
        scored = _make_scored(
            "s4",
            "CMS proposes new Star Ratings methodology for Medicare Advantage",
            published=datetime(2024, 1, 1),
            score=0.01,  # deliberately stale/wrong
            categories=["financial_pressure"],  # deliberately wrong
            entities=[],  # deliberately missing
        )
        temp_db.upsert_story(scored, primary_category="financial_pressure", states=[])

        report = backfill_categories(temp_db, sample_config, rescore=True)

        assert report["changed"] == 1
        row = temp_db.get_story("s4")
        assert row["primary_category"] == "policy_regulatory"
        # Scoring fields now reflect a fresh score_item() pass, not the stale seed.
        assert row["relevance_score"] != 0.01
        assert row["relevance_score"] > 0.0

    def test_dry_run_writes_nothing(self, temp_db, sample_config):
        """--dry-run reports the would-be change but leaves the DB untouched."""
        scored = _make_scored(
            "s5",
            "CMS proposes new Star Ratings methodology for Medicare Advantage",
            published=datetime(2024, 1, 1),
            score=0.5,
            categories=["financial_pressure"],
        )
        temp_db.upsert_story(scored, primary_category="financial_pressure")

        report = backfill_categories(temp_db, sample_config, dry_run=True)

        assert report["changed"] == 1
        row = temp_db.get_story("s5")
        # Still the stale value — dry-run never wrote.
        assert row["primary_category"] == "financial_pressure"
        assert json.loads(row["categories"]) == ["financial_pressure"]

    def test_second_run_is_idempotent(self, temp_db, sample_config):
        """Once corrected, re-running the backfill reports zero further changes."""
        scored = _make_scored(
            "s6",
            "CMS proposes new Star Ratings methodology for Medicare Advantage",
            published=datetime(2024, 1, 1),
            score=0.5,
            categories=["financial_pressure"],
        )
        temp_db.upsert_story(scored, primary_category="financial_pressure")

        first = backfill_categories(temp_db, sample_config)
        assert first["changed"] == 1

        second = backfill_categories(temp_db, sample_config)
        assert second["changed"] == 0
        assert second["unchanged"] == 1

    def test_gated_row_stays_uncategorized_and_is_counted(self, temp_db, sample_config):
        """A broad low-priority source with no MA context is gated, not miscounted."""
        # priority 1 < ma_context_min_priority (3); no watched entity or MA term
        # in the text, so scoring.py's MA-context gate suppresses keyword scoring.
        scored = _make_scored(
            "s7",
            "Hospital opens new parking garage",
            published=datetime(2024, 1, 1),
            score=0.05,
            categories=[],
            source_priority=1,
        )
        # Deliberately stale wrong category so a change is also exercised.
        temp_db.upsert_story(scored, primary_category="financial_pressure")

        report = backfill_categories(temp_db, sample_config)

        assert report["gated"] == 1
        assert report["changed"] == 1
        assert report["cat_to_uncategorized"] == 1
        row = temp_db.get_story("s7")
        assert row["primary_category"] == "uncategorized"

    def test_duplicate_rows_are_reclassified(self, temp_db, sample_config):
        """Near-duplicate rows (duplicate_of set) are included and corrected too."""
        rep = _make_scored(
            "rep",
            "UnitedHealthcare expands Medicare Advantage enrollment to new counties",
            published=datetime(2024, 1, 1),
            score=0.5,
            categories=["membership_movement"],
        )
        temp_db.upsert_story(rep, primary_category="membership_movement")

        dup = _make_scored(
            "dup",
            "CMS proposes new Star Ratings methodology for Medicare Advantage",
            published=datetime(2024, 1, 2),
            score=0.5,
            categories=["financial_pressure"],  # stale/wrong
        )
        temp_db.upsert_story(
            dup, primary_category="financial_pressure", duplicate_of="rep"
        )

        # The duplicate is hidden from the default browsable view...
        assert [r["item_id"] for r in temp_db.get_stories()] == ["rep"]

        report = backfill_categories(temp_db, sample_config)

        # Both rows examined regardless of duplicate_of.
        assert report["total"] == 2
        dup_row = temp_db.get_story("dup")
        assert dup_row["primary_category"] == "policy_regulatory"
        assert dup_row["duplicate_of"] == "rep"

    def test_limit_caps_rows_examined(self, temp_db, sample_config):
        """`limit` restricts how many rows are scanned."""
        for i in range(5):
            scored = _make_scored(
                f"item{i}",
                "Hospital opens new parking garage",
                published=datetime(2024, 1, i + 1),
                score=0.1,
            )
            temp_db.upsert_story(scored, primary_category="uncategorized")

        report = backfill_categories(temp_db, sample_config, limit=2)
        assert report["total"] == 2


class TestBackfillCli:
    """CLI argument parsing / entry point."""

    def test_cli_dry_run_smoke(self, project_root_with_config, capsys):
        """main() loads config, opens the DB, runs a dry-run pass, and exits 0."""
        from ma_signal_monitor.config import load_config
        from ma_signal_monitor.storage import StateStore

        config = load_config(project_root_with_config)
        store = StateStore(project_root_with_config / config.db_path)
        try:
            scored = _make_scored(
                "s1",
                "Membership enrollment grows in new markets",
                published=datetime(2024, 1, 1),
                score=0.5,
                categories=[],
            )
            store.upsert_story(scored, primary_category="uncategorized")
        finally:
            store.close()

        exit_code = main(["--root", str(project_root_with_config), "--dry-run"])

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "Examined 1 stories" in out
        assert "[DRY RUN]" in out

        # dry-run really didn't write.
        store2 = StateStore(project_root_with_config / config.db_path)
        try:
            assert store2.get_story("s1")["primary_category"] == "uncategorized"
        finally:
            store2.close()

    def test_cli_limit_flag_parses(self, project_root_with_config, capsys):
        """--limit is accepted and threaded through to backfill_categories."""
        exit_code = main(
            ["--root", str(project_root_with_config), "--dry-run", "--limit", "0"]
        )
        assert exit_code == 0
        assert "Examined 0 stories" in capsys.readouterr().out
