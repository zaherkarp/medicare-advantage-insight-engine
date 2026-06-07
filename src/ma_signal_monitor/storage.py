"""SQLite-based persistence for state, deduplication, delivery logs, and the
browsable story archive that backs the web product."""

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from ma_signal_monitor.models import DeliveryResult, ScoredItem

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

-- Browsable archive of scored stories. Populated mid-pipeline (one row per
-- item the first run it is seen). This is what the web frontend reads.
CREATE TABLE IF NOT EXISTS stories (
    item_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    link TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_priority INTEGER,
    summary TEXT,
    published_date TEXT,
    fetched_at TEXT NOT NULL,
    relevance_score REAL,
    primary_category TEXT,
    categories TEXT,
    entities TEXT,
    states TEXT,
    public_draft TEXT
);

-- Generated Daily Briefing digests, archived for the /briefing page and so
-- email sends are idempotent (one digest per UTC day).
CREATE TABLE IF NOT EXISTS digests (
    digest_date TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    story_count INTEGER NOT NULL,
    subject TEXT NOT NULL,
    html TEXT NOT NULL,
    sent_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_seen_items_first_seen ON seen_items(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_delivery_log_timestamp ON delivery_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_stories_published ON stories(published_date);
CREATE INDEX IF NOT EXISTS idx_stories_category ON stories(primary_category);
CREATE INDEX IF NOT EXISTS idx_stories_score ON stories(relevance_score);
CREATE INDEX IF NOT EXISTS idx_stories_fetched ON stories(fetched_at);
"""


class StateStore:
    """SQLite-backed state store for the application."""

    def __init__(self, db_path: str | Path, read_only: bool = False):
        self.db_path = Path(db_path)
        self.read_only = read_only
        self.fts_enabled = False
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        if not read_only:
            self._init_db()
        else:
            self._init_fts()

    def _init_db(self) -> None:
        """Initialize the database schema."""
        conn = self._get_conn()
        # WAL lets the web reader and the scheduled writer share the file
        # without blocking each other.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        self._init_fts()
        logger.debug("Database initialized at %s", self.db_path)

    def _init_fts(self) -> None:
        """Set up the FTS5 full-text index, falling back gracefully.

        FTS5 is enabled in most SQLite builds, but not guaranteed — if the
        virtual table can't be created, search degrades to a LIKE scan.
        """
        conn = self._get_conn()
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS stories_fts "
                "USING fts5(item_id UNINDEXED, title, summary)"
            )
            conn.commit()
            self.fts_enabled = True
            if not self.read_only:
                self._backfill_fts()
        except sqlite3.OperationalError as e:
            self.fts_enabled = False
            logger.warning("FTS5 unavailable; search will use LIKE fallback: %s", e)

    def _backfill_fts(self) -> None:
        """Populate the FTS index from existing stories if it's empty.

        Handles databases created before the index existed (Phase 1/2 archives).
        """
        conn = self._get_conn()
        fts_count = conn.execute("SELECT COUNT(*) FROM stories_fts").fetchone()[0]
        story_count = conn.execute("SELECT COUNT(*) FROM stories").fetchone()[0]
        if fts_count == 0 and story_count > 0:
            conn.execute(
                "INSERT INTO stories_fts (item_id, title, summary) "
                "SELECT item_id, title, summary FROM stories"
            )
            conn.commit()
            logger.info("Backfilled FTS index with %d stories", story_count)

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create the database connection."""
        if self._conn is None:
            # check_same_thread=False so a single store can serve the web app
            # across request threads; SQLite serializes writes internally.
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
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
            (
                item_id,
                source_name,
                title,
                link,
                datetime.utcnow().isoformat(),
                relevance_score,
            ),
        )
        conn.commit()

    def get_seen_count(self) -> int:
        """Return the number of seen items."""
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) FROM seen_items").fetchone()
        return row[0]

    # --- Story Archive (web frontend) ---

    def upsert_story(
        self,
        scored: ScoredItem,
        primary_category: str,
        public_draft: dict | None = None,
        states: list[str] | None = None,
    ) -> None:
        """Persist a scored item into the browsable story archive.

        Reuses the fields already computed on the ScoredItem. List/dict fields
        are stored as JSON. Idempotent via INSERT OR REPLACE on item_id.
        """
        conn = self._get_conn()
        item = scored.item
        conn.execute(
            """INSERT OR REPLACE INTO stories
               (item_id, title, link, source_name, source_priority, summary,
                published_date, fetched_at, relevance_score, primary_category,
                categories, entities, states, public_draft)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item.item_id,
                item.title,
                item.link,
                item.source_name,
                item.source_priority,
                item.summary,
                item.published_date.isoformat() if item.published_date else None,
                item.fetched_at.isoformat(),
                scored.relevance_score,
                primary_category,
                json.dumps(scored.matched_categories),
                json.dumps(scored.matched_entities),
                json.dumps(states or []),
                json.dumps(public_draft) if public_draft else None,
            ),
        )
        if self.fts_enabled:
            # Keep the FTS row in sync (delete-then-insert mirrors the upsert).
            conn.execute("DELETE FROM stories_fts WHERE item_id = ?", (item.item_id,))
            conn.execute(
                "INSERT INTO stories_fts (item_id, title, summary) VALUES (?, ?, ?)",
                (item.item_id, item.title, item.summary or ""),
            )
        conn.commit()

    @staticmethod
    def _fts_query(query: str) -> str:
        """Build a safe FTS5 MATCH expression (AND of quoted prefix terms)."""
        import re

        terms = re.findall(r"\w+", query)
        # Quote each term and add a prefix wildcard for partial matches.
        return " AND ".join(f'"{t}"*' for t in terms)

    def search_stories(
        self, query: str, limit: int = 25, offset: int = 0
    ) -> list[sqlite3.Row]:
        """Full-text search over story titles and summaries.

        Uses FTS5 (ranked by relevance) when available, else a LIKE scan.
        """
        conn = self._get_conn()
        if self.fts_enabled:
            match = self._fts_query(query)
            if not match:
                return []
            return conn.execute(
                """SELECT s.* FROM stories_fts f
                   JOIN stories s ON s.item_id = f.item_id
                   WHERE stories_fts MATCH ?
                   ORDER BY rank LIMIT ? OFFSET ?""",
                (match, limit, offset),
            ).fetchall()
        like = f"%{query.strip()}%"
        return conn.execute(
            """SELECT * FROM stories
               WHERE title LIKE ? OR summary LIKE ?
               ORDER BY COALESCE(published_date, fetched_at) DESC
               LIMIT ? OFFSET ?""",
            (like, like, limit, offset),
        ).fetchall()

    def count_search(self, query: str) -> int:
        """Count full-text search matches (for pagination)."""
        conn = self._get_conn()
        if self.fts_enabled:
            match = self._fts_query(query)
            if not match:
                return 0
            return conn.execute(
                "SELECT COUNT(*) FROM stories_fts WHERE stories_fts MATCH ?",
                (match,),
            ).fetchone()[0]
        like = f"%{query.strip()}%"
        return conn.execute(
            "SELECT COUNT(*) FROM stories WHERE title LIKE ? OR summary LIKE ?",
            (like, like),
        ).fetchone()[0]

    @staticmethod
    def _story_filters(category: str | None, state: str | None) -> tuple[str, list]:
        """Build a shared WHERE clause for story queries."""
        clauses: list[str] = []
        params: list = []
        if category:
            clauses.append("primary_category = ?")
            params.append(category)
        if state:
            # states is a JSON array of USPS codes; match the quoted token.
            clauses.append("states LIKE ?")
            params.append(f'%"{state}"%')
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    def get_stories(
        self,
        category: str | None = None,
        state: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> list[sqlite3.Row]:
        """Return stories in reverse-chronological order, optionally filtered.

        Dateless items (no published_date) fall back to fetched_at so they
        still sort sensibly.
        """
        conn = self._get_conn()
        where, params = self._story_filters(category, state)
        rows = conn.execute(
            f"""SELECT * FROM stories{where}
                ORDER BY COALESCE(published_date, fetched_at) DESC
                LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()
        return rows

    def count_stories(
        self, category: str | None = None, state: str | None = None
    ) -> int:
        """Count stories matching the given filters (for pagination)."""
        conn = self._get_conn()
        where, params = self._story_filters(category, state)
        row = conn.execute(f"SELECT COUNT(*) FROM stories{where}", params).fetchone()
        return row[0]

    def get_story(self, item_id: str) -> sqlite3.Row | None:
        """Return a single story by id, or None."""
        conn = self._get_conn()
        return conn.execute(
            "SELECT * FROM stories WHERE item_id = ?", (item_id,)
        ).fetchone()

    def get_recent_top_stories(
        self,
        since: datetime,
        limit: int = 12,
        min_score: float = 0.0,
    ) -> list[sqlite3.Row]:
        """Return the highest-scoring stories since `since` (for the digest).

        Ordered by relevance score, then recency. Uses
        COALESCE(published_date, fetched_at) so dateless items are still
        windowed sensibly.
        """
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT * FROM stories
               WHERE COALESCE(published_date, fetched_at) >= ?
                 AND relevance_score >= ?
               ORDER BY relevance_score DESC,
                        COALESCE(published_date, fetched_at) DESC
               LIMIT ?""",
            (since.isoformat(), min_score, limit),
        ).fetchall()
        return rows

    # --- Daily Briefing digests ---

    def save_digest(
        self,
        digest_date: str,
        generated_at: str,
        story_count: int,
        subject: str,
        html: str,
        sent_at: str | None = None,
    ) -> None:
        """Persist (or replace) a generated digest for a given UTC day."""
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO digests
               (digest_date, generated_at, story_count, subject, html, sent_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (digest_date, generated_at, story_count, subject, html, sent_at),
        )
        conn.commit()

    def mark_digest_sent(self, digest_date: str, sent_at: str) -> None:
        """Record that a digest was emailed."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE digests SET sent_at = ? WHERE digest_date = ?",
            (sent_at, digest_date),
        )
        conn.commit()

    def get_digest(self, digest_date: str) -> sqlite3.Row | None:
        """Return a digest by its UTC date string, or None."""
        conn = self._get_conn()
        return conn.execute(
            "SELECT * FROM digests WHERE digest_date = ?", (digest_date,)
        ).fetchone()

    def get_latest_digest(self) -> sqlite3.Row | None:
        """Return the most recently dated digest, or None."""
        conn = self._get_conn()
        return conn.execute(
            "SELECT * FROM digests ORDER BY digest_date DESC LIMIT 1"
        ).fetchone()

    def list_digests(self, limit: int = 30) -> list[sqlite3.Row]:
        """Return recent digests (date + metadata only) for the archive list."""
        conn = self._get_conn()
        return conn.execute(
            """SELECT digest_date, generated_at, story_count, subject, sent_at
               FROM digests ORDER BY digest_date DESC LIMIT ?""",
            (limit,),
        ).fetchall()

    def get_state_counts(self) -> dict[str, int]:
        """Return a {state_code: story_count} map across the archive.

        Each story's `states` JSON array may contain several codes; every code
        is counted. Used by the State Intelligence overview.
        """
        conn = self._get_conn()
        counts: dict[str, int] = {}
        for row in conn.execute("SELECT states FROM stories WHERE states IS NOT NULL"):
            for code in json.loads(row[0] or "[]"):
                counts[code] = counts.get(code, 0) + 1
        return counts

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

    def get_last_run(self) -> sqlite3.Row | None:
        """Return the most recent completed run's metadata, or None."""
        conn = self._get_conn()
        return conn.execute(
            "SELECT * FROM run_metadata WHERE run_end IS NOT NULL "
            "ORDER BY run_end DESC LIMIT 1"
        ).fetchone()

    def get_category_counts(self) -> dict[str, int]:
        """Return {primary_category: story_count} across the archive."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT primary_category AS cat, COUNT(*) AS n FROM stories "
            "GROUP BY primary_category"
        ).fetchall()
        return {(r["cat"] or "uncategorized"): r["n"] for r in rows}

    def get_source_counts(self) -> dict[str, int]:
        """Return {source_name: story_count} across the archive."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT source_name AS s, COUNT(*) AS n FROM stories GROUP BY source_name"
        ).fetchall()
        return {r["s"]: r["n"] for r in rows}

    # --- Cleanup ---

    def cleanup_old_records(
        self,
        seen_retention_days: int = 90,
        log_retention_days: int = 30,
        story_retention_days: int = 365,
    ) -> tuple[int, int, int]:
        """Remove old records. Returns (seen_deleted, logs_deleted, stories_deleted).

        The story archive is kept far longer than dedup rows (default 1 year)
        since it backs the browsable site. Pass story_retention_days=0 to keep
        the archive forever.
        """
        conn = self._get_conn()

        seen_cutoff = (
            datetime.utcnow() - timedelta(days=seen_retention_days)
        ).isoformat()
        cursor = conn.execute(
            "DELETE FROM seen_items WHERE first_seen_at < ?", (seen_cutoff,)
        )
        seen_deleted = cursor.rowcount

        log_cutoff = (
            datetime.utcnow() - timedelta(days=log_retention_days)
        ).isoformat()
        cursor = conn.execute(
            "DELETE FROM delivery_log WHERE timestamp < ?", (log_cutoff,)
        )
        logs_deleted = cursor.rowcount

        stories_deleted = 0
        if story_retention_days > 0:
            story_cutoff = (
                datetime.utcnow() - timedelta(days=story_retention_days)
            ).isoformat()
            cursor = conn.execute(
                "DELETE FROM stories WHERE fetched_at < ?", (story_cutoff,)
            )
            stories_deleted = cursor.rowcount
            if self.fts_enabled and stories_deleted:
                # Drop orphaned FTS rows for the deleted stories.
                conn.execute(
                    "DELETE FROM stories_fts WHERE item_id NOT IN "
                    "(SELECT item_id FROM stories)"
                )

        conn.commit()
        logger.info(
            "Cleanup: removed %d old seen items, %d old delivery logs, %d old stories",
            seen_deleted,
            logs_deleted,
            stories_deleted,
        )
        return seen_deleted, logs_deleted, stories_deleted
