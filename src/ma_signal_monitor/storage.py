"""SQLite-based persistence for state, deduplication, and delivery logs."""

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from ma_signal_monitor.models import DeliveryResult

logger = logging.getLogger("ma_signal_monitor.storage")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS seen_items (
    item_id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    title TEXT NOT NULL,
    link TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    relevance_score REAL
);

CREATE TABLE IF NOT EXISTS delivery_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_title TEXT NOT NULL,
    success INTEGER NOT NULL,
    status_code INTEGER,
    error TEXT,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_start TEXT NOT NULL,
    run_end TEXT,
    items_fetched INTEGER DEFAULT 0,
    items_new INTEGER DEFAULT 0,
    items_relevant INTEGER DEFAULT 0,
    alerts_sent INTEGER DEFAULT 0,
    errors INTEGER DEFAULT 0,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_seen_items_first_seen ON seen_items(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_delivery_log_timestamp ON delivery_log(timestamp);
"""


class StateStore:
    """SQLite-backed state store for the application."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the database schema."""
        conn = self._get_conn()
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        logger.debug("Database initialized at %s", self.db_path)

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create the database connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    # --- Deduplication ---

    def is_seen(self, item_id: str) -> bool:
        """Check if an item ID has been seen before."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM seen_items WHERE item_id = ?", (item_id,)
        ).fetchone()
        return row is not None

    def mark_seen(
        self,
        item_id: str,
        source_name: str,
        title: str,
        link: str,
        relevance_score: float | None = None,
    ) -> None:
        """Record an item as seen."""
        conn = self._get_conn()
        conn.execute(
            """INSERT OR IGNORE INTO seen_items
               (item_id, source_name, title, link, first_seen_at, relevance_score)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (item_id, source_name, title, link, datetime.utcnow().isoformat(), relevance_score),
        )
        conn.commit()

    def get_seen_count(self) -> int:
        """Return the number of seen items."""
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) FROM seen_items").fetchone()
        return row[0]

    # --- Delivery Logging ---

    def log_delivery(self, result: DeliveryResult) -> None:
        """Log a delivery attempt."""
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO delivery_log (alert_title, success, status_code, error, timestamp)
               VALUES (?, ?, ?, ?, ?)""",
            (
                result.alert_title,
                1 if result.success else 0,
                result.status_code,
                result.error,
                result.timestamp.isoformat(),
            ),
        )
        conn.commit()

    # --- Run Metadata ---

    def start_run(self) -> int:
        """Record the start of a processing run. Returns the run ID."""
        conn = self._get_conn()
        cursor = conn.execute(
            "INSERT INTO run_metadata (run_start) VALUES (?)",
            (datetime.utcnow().isoformat(),),
        )
        conn.commit()
        return cursor.lastrowid

    def end_run(
        self,
        run_id: int,
        items_fetched: int = 0,
        items_new: int = 0,
        items_relevant: int = 0,
        alerts_sent: int = 0,
        errors: int = 0,
        notes: str = "",
    ) -> None:
        """Record the end of a processing run."""
        conn = self._get_conn()
        conn.execute(
            """UPDATE run_metadata
               SET run_end = ?, items_fetched = ?, items_new = ?,
                   items_relevant = ?, alerts_sent = ?, errors = ?, notes = ?
               WHERE id = ?""",
            (
                datetime.utcnow().isoformat(),
                items_fetched,
                items_new,
                items_relevant,
                alerts_sent,
                errors,
                notes,
                run_id,
            ),
        )
        conn.commit()

    # --- Cleanup ---

    def cleanup_old_records(
        self, seen_retention_days: int = 90, log_retention_days: int = 30
    ) -> tuple[int, int]:
        """Remove old seen items and delivery logs. Returns (seen_deleted, logs_deleted)."""
        conn = self._get_conn()

        seen_cutoff = (datetime.utcnow() - timedelta(days=seen_retention_days)).isoformat()
        cursor = conn.execute(
            "DELETE FROM seen_items WHERE first_seen_at < ?", (seen_cutoff,)
        )
        seen_deleted = cursor.rowcount

        log_cutoff = (datetime.utcnow() - timedelta(days=log_retention_days)).isoformat()
        cursor = conn.execute(
            "DELETE FROM delivery_log WHERE timestamp < ?", (log_cutoff,)
        )
        logs_deleted = cursor.rowcount

        conn.commit()
        logger.info(
            "Cleanup: removed %d old seen items, %d old delivery logs",
            seen_deleted, logs_deleted,
        )
        return seen_deleted, logs_deleted
