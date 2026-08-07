"""Tests for scripts/archive_guard.py, the CI archive-restore safety guard."""

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# scripts/ isn't an installed package; add it to sys.path for direct import,
# mirroring the sys.path insertion scripts themselves use for src/ (see e.g.
# scripts/scorecard.py). ruff's E402 explicitly exempts sys.path edits.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import archive_guard

from ma_signal_monitor.models import NormalizedItem, ScoredItem
from ma_signal_monitor.storage import StateStore


def _seed(db_path: Path, n: int) -> None:
    """Populate `db_path`'s real `stories` table via StateStore.

    Uses the project's actual schema/write path (not a hand-rolled table) so
    these tests track storage.py rather than a parallel guess at its shape.
    `n=0` still creates the schema with zero rows -- exactly the shape of the
    silent-empty-restore bug this guard exists to catch.
    """
    store = StateStore(db_path)
    for i in range(n):
        item = NormalizedItem(
            item_id=f"item{i}",
            source_name="Test Feed",
            source_type="rss",
            source_priority=3,
            source_tags=["test"],
            title=f"Story {i}",
            link=f"https://example.com/{i}",
            published_date=datetime(2024, 1, 1),
            summary="summary",
        )
        scored = ScoredItem(
            item=item, relevance_score=0.5, matched_categories=[], matched_entities=[]
        )
        store.upsert_story(scored, primary_category="uncategorized")
    store.close()


class TestValidate:
    """`archive_guard.py validate <db_path>`."""

    def test_valid_archive_passes_and_reports_row_count(self, tmp_path, capsys):
        db_path = tmp_path / "archive.db"
        _seed(db_path, 5)

        exit_code = archive_guard.main(["validate", str(db_path)])

        assert exit_code == 0
        assert capsys.readouterr().out.strip() == "5"

    def test_missing_file_fails(self, tmp_path, capsys):
        db_path = tmp_path / "nope.db"

        exit_code = archive_guard.main(["validate", str(db_path)])

        assert exit_code == 1
        assert "does not exist" in capsys.readouterr().err

    def test_corrupt_truncated_file_fails(self, tmp_path, capsys):
        """Random bytes at a .db path fail integrity, not crash the script."""
        db_path = tmp_path / "corrupt.db"
        db_path.write_bytes(b"not a real sqlite file" * 50)

        exit_code = archive_guard.main(["validate", str(db_path)])

        assert exit_code == 1
        assert "not a usable SQLite database" in capsys.readouterr().err

    def test_valid_sqlite_missing_core_table_fails(self, tmp_path, capsys):
        """An empty-but-valid SQLite file lacking `stories` still fails."""
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE unrelated (x INTEGER)")
        conn.commit()
        conn.close()

        exit_code = archive_guard.main(["validate", str(db_path)])

        assert exit_code == 1
        assert "stories" in capsys.readouterr().err


class TestRowcount:
    """`archive_guard.py rowcount <db_path>`."""

    def test_reports_row_count(self, tmp_path, capsys):
        db_path = tmp_path / "archive.db"
        _seed(db_path, 3)

        exit_code = archive_guard.main(["rowcount", str(db_path)])

        assert exit_code == 0
        assert capsys.readouterr().out.strip() == "3"

    def test_missing_file_reports_zero_and_still_exits_clean(self, tmp_path, capsys):
        db_path = tmp_path / "nope.db"

        exit_code = archive_guard.main(["rowcount", str(db_path)])

        assert exit_code == 0
        assert capsys.readouterr().out.strip() == "0"


class TestCompare:
    """`archive_guard.py compare --before N --after <db_path>`."""

    def test_passes_when_rows_grew(self, tmp_path):
        db_path = tmp_path / "archive.db"
        _seed(db_path, 10)

        exit_code = archive_guard.main(
            ["compare", "--before", "5", "--after", str(db_path)]
        )

        assert exit_code == 0

    def test_passes_when_rows_stayed_equal(self, tmp_path):
        db_path = tmp_path / "archive.db"
        _seed(db_path, 5)

        exit_code = archive_guard.main(
            ["compare", "--before", "5", "--after", str(db_path)]
        )

        assert exit_code == 0

    def test_passes_within_retention_pruning_tolerance(self, tmp_path):
        """A small drop (well under the tolerance) does not trip the guard."""
        db_path = tmp_path / "archive.db"
        _seed(db_path, 98)  # 2% drop from 100

        exit_code = archive_guard.main(
            ["compare", "--before", "100", "--after", str(db_path)]
        )

        assert exit_code == 0

    def test_fails_on_catastrophic_drop(self, tmp_path, capsys):
        """A populated archive collapsing to (near) zero fails loudly."""
        db_path = tmp_path / "archive.db"
        _seed(db_path, 0)

        exit_code = archive_guard.main(
            ["compare", "--before", "500", "--after", str(db_path)]
        )

        assert exit_code == 1
        assert "CATASTROPHIC" in capsys.readouterr().err

    def test_cold_start_with_zero_before_passes(self, tmp_path):
        """No prior archive (before=0) is a legitimate first run, not a failure."""
        db_path = tmp_path / "archive.db"
        _seed(db_path, 0)

        exit_code = archive_guard.main(
            ["compare", "--before", "0", "--after", str(db_path)]
        )

        assert exit_code == 0
