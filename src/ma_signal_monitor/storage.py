"""SQLite-based persistence for state, deduplication, delivery logs, and the
browsable story archive that backs the web product."""

import json
import logging
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from ma_signal_monitor.models import DeliveryResult, ScoredItem

logger = logging.getLogger("ma_signal_monitor.storage")

# Allowed reader-feedback verdicts. Kept small and structured (not free-form)
# so feedback feeds directly into the keyword-mining and source-yield loops.
# The alert_* verdicts are a distinct axis from the rest: they assert an
# outcome about a specific *posted-or-not-posted alert* (see
# StateStore.get_alert_delivered), not a general opinion about the story.
VALID_VERDICTS = frozenset(
    {
        "relevant",
        "irrelevant",
        "wrong_category",
        "great",
        "alert_correct",
        "alert_false_positive",
        "alert_missed",
    }
)
# Verdicts specifically about alert-posting outcomes (feature: feedback loop
# on scoring accuracy). Kept separate from VALID_VERDICTS' full set so
# queries can select just this axis without hardcoding the prefix elsewhere.
ALERT_VERDICTS = frozenset({"alert_correct", "alert_false_positive", "alert_missed"})
# Channels whose votes are owner ground-truth (weight 1.0). Everything else is
# advisory crowd signal that surfaces things for review but never auto-mutates.
OWNER_CHANNELS = frozenset({"local_web", "ntfy", "cli"})

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
    timestamp TEXT NOT NULL,
    -- The archived story this delivery attempt posted (or tried to post).
    -- NULL on rows written before this column existed. Added to existing DBs
    -- by _ensure_column (see _init_db). Lets alert-outcome feedback (see the
    -- `feedback` table below) confirm whether a given story was actually
    -- posted to the webhook before a human labels it correct/false_positive.
    item_id TEXT
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
    public_draft TEXT,
    -- item_id of the representative story this one near-duplicates (same story
    -- carried by another source); NULL = this row IS the representative/unique.
    -- Added to existing DBs by _ensure_column (see _init_db).
    duplicate_of TEXT,
    -- JSON list of the scorer's per-factor breakdown (ScoringReason objects:
    -- factor/detail/contribution) at the time this story was scored. Lets
    -- alert-outcome feedback trace a label back to exactly which scoring
    -- factors produced the combined relevance_score. Added by _ensure_column.
    scoring_breakdown TEXT,
    -- config.min_relevance_score in effect when this story was scored. The
    -- threshold moves over time as taxonomy.yaml is tuned, so this snapshot
    -- is what makes a later "was this alert-worthy" judgment meaningful.
    -- Added by _ensure_column.
    threshold_at_score REAL
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

-- Source discovery: outbound domains harvested from story links, accumulated
-- over runs and ranked by relevance-weighted frequency.
CREATE TABLE IF NOT EXISTS candidate_domains (
    domain TEXT PRIMARY KEY,
    times_seen INTEGER NOT NULL DEFAULT 0,
    relevance_score REAL NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    example_link TEXT,
    example_story_id TEXT,
    feeds_checked_at TEXT,
    status TEXT NOT NULL DEFAULT 'new'
);

-- Source discovery: feeds autodiscovered on candidate domains, awaiting review
-- or already promoted into the live source list (merged at config load time).
CREATE TABLE IF NOT EXISTS candidate_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_url TEXT UNIQUE NOT NULL,
    domain TEXT NOT NULL,
    feed_title TEXT,
    discovery_method TEXT,
    times_seen INTEGER NOT NULL DEFAULT 0,
    relevance_score REAL NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new'
);

-- Reader feedback on stories. Append-only audit log written by every channel
-- (local web buttons, ntfy actions, CLI, and later crowd reactions via giscus).
-- `weight` separates ground-truth owner votes (1.0) from advisory crowd signal
-- (< 1.0). `source_ref` makes crowd re-ingest idempotent via the partial unique
-- index below. Rows are never mutated after insert.
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'local_web',
    voter_key TEXT,
    weight REAL NOT NULL DEFAULT 1.0,
    suggested_category TEXT,
    comment TEXT,
    source_ref TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidate_domains_rank ON candidate_domains(status, relevance_score);
CREATE INDEX IF NOT EXISTS idx_candidate_sources_rank ON candidate_sources(status, relevance_score);
CREATE INDEX IF NOT EXISTS idx_feedback_item ON feedback(item_id);
-- Idempotent crowd ingest: one row per (channel, source_ref) when a ref is set.
CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_source_ref
    ON feedback(channel, source_ref) WHERE source_ref IS NOT NULL;

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
        self._migrate()
        self._init_fts()
        self._clean_story_titles()
        logger.debug("Database initialized at %s", self.db_path)

    def _ensure_column(self, table: str, column: str, decl: str) -> bool:
        """Add ``column`` to ``table`` if it's missing. Returns True if added.

        ``CREATE TABLE IF NOT EXISTS`` never alters an existing table, so a new
        column on a carried-forward production DB needs an explicit, guarded
        ``ALTER TABLE``. Idempotent: a PRAGMA check makes re-running a no-op.
        Table/column/decl are code-controlled constants (never user input).
        """
        conn = self._get_conn()
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column in existing:
            return False
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        conn.commit()
        logger.info("Migrated: added %s.%s column", table, column)
        return True

    def _migrate(self) -> None:
        """Apply in-place column migrations to a carried-forward DB.

        Each step is guarded/idempotent (see :meth:`_ensure_column`), so this
        runs safely on every open. Indexes on migrated columns are created here
        (not in SCHEMA_SQL) because the column may not exist yet when the schema
        script runs against an old DB.
        """
        conn = self._get_conn()
        self._ensure_column("stories", "duplicate_of", "TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_stories_duplicate_of "
            "ON stories(duplicate_of)"
        )
        self._ensure_column("stories", "scoring_breakdown", "TEXT")
        self._ensure_column("stories", "threshold_at_score", "REAL")
        self._ensure_column("delivery_log", "item_id", "TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_delivery_log_item_id "
            "ON delivery_log(item_id)"
        )
        conn.commit()

    def _clean_story_titles(self) -> None:
        """Strip HTML from any already-stored titles (one-time self-heal).

        Pre-fix archives may contain titles with embedded markup (e.g. an
        ``<a>`` tag from Fierce Healthcare). This cleans them in place and keeps
        the FTS index in sync. Cheap — usually matches zero rows.
        """
        from ma_signal_monitor.normalize import strip_html

        conn = self._get_conn()
        rows = conn.execute(
            "SELECT item_id, title FROM stories WHERE title LIKE '%<%'"
        ).fetchall()
        if not rows:
            return
        for r in rows:
            clean = strip_html(r["title"])
            conn.execute(
                "UPDATE stories SET title = ? WHERE item_id = ?",
                (clean, r["item_id"]),
            )
            if self.fts_enabled:
                conn.execute(
                    "UPDATE stories_fts SET title = ? WHERE item_id = ?",
                    (clean, r["item_id"]),
                )
        conn.commit()
        logger.info("Cleaned HTML from %d existing story titles", len(rows))

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
        duplicate_of: str | None = None,
        threshold_at_score: float | None = None,
    ) -> None:
        """Persist a scored item into the browsable story archive.

        Reuses the fields already computed on the ScoredItem, including its
        ``reasons`` (the scorer's per-factor breakdown), stored as JSON so a
        later alert-outcome label can be traced back to exactly which scoring
        factors produced the combined score. ``threshold_at_score`` is the
        caller's ``config.min_relevance_score`` at scoring time (the
        threshold moves as taxonomy.yaml is tuned, so this is a snapshot, not
        a live lookup). List/dict fields are stored as JSON. Idempotent via
        INSERT OR REPLACE on item_id. ``duplicate_of`` is the item_id of the
        representative story this one near-duplicates (None = it is a
        representative / unique).
        """
        conn = self._get_conn()
        item = scored.item
        conn.execute(
            """INSERT OR REPLACE INTO stories
               (item_id, title, link, source_name, source_priority, summary,
                published_date, fetched_at, relevance_score, primary_category,
                categories, entities, states, public_draft, duplicate_of,
                scoring_breakdown, threshold_at_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                duplicate_of,
                json.dumps([asdict(r) for r in scored.reasons]),
                threshold_at_score,
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

    def update_story_classification(
        self, item_id: str, primary_category: str, categories: list[str]
    ) -> None:
        """Rewrite a story's category fields (the reclassification backfill).

        Used by :mod:`ma_signal_monitor.backfill` to move an archived row onto
        today's taxonomy without touching anything else. Deliberately narrow
        — see that module's docstring for why ``relevance_score``/``entities``/
        ``states`` are a separate, opt-in write (:meth:`update_story_scoring`).
        ``stories_fts`` indexes only title/summary, so no FTS maintenance is
        needed here. Does not commit: the backfill CLI processes the whole
        archive and batches commits itself (see ``_clean_story_titles`` for
        the same caller-commits pattern).
        """
        conn = self._get_conn()
        conn.execute(
            "UPDATE stories SET primary_category = ?, categories = ? WHERE item_id = ?",
            (primary_category, json.dumps(categories), item_id),
        )

    def update_story_scoring(
        self,
        item_id: str,
        relevance_score: float,
        entities: list[str],
        states: list[str],
    ) -> None:
        """Rewrite a story's scoring fields (the backfill's ``--rescore`` path).

        Companion to :meth:`update_story_classification` for the opt-in
        rescore mode, which additionally refreshes the fields that can move a
        story across ``archive_min_score``/digest visibility thresholds. Same
        caller-commits convention (no commit here).
        """
        conn = self._get_conn()
        conn.execute(
            "UPDATE stories SET relevance_score = ?, entities = ?, states = ? "
            "WHERE item_id = ?",
            (relevance_score, json.dumps(entities), json.dumps(states), item_id),
        )

    @staticmethod
    def _fts_query(query: str) -> str:
        """Build a safe FTS5 MATCH expression (AND of quoted prefix terms)."""
        import re

        terms = re.findall(r"\w+", query)
        # Quote each term and add a prefix wildcard for partial matches.
        return " AND ".join(f'"{t}"*' for t in terms)

    def search_stories(
        self, query: str, limit: int = 25, offset: int = 0, min_score: float = 0.0
    ) -> list[sqlite3.Row]:
        """Full-text search over story titles and summaries.

        Uses FTS5 (ranked by relevance) when available, else a LIKE scan.
        ``min_score`` applies the same archive floor as the feed so search
        results don't resurface sub-floor noise.
        """
        conn = self._get_conn()
        floored = min_score > 0.0
        if self.fts_enabled:
            match = self._fts_query(query)
            if not match:
                return []
            score_clause = " AND COALESCE(s.relevance_score, 0) >= ?" if floored else ""
            params = (
                (match, min_score, limit, offset)
                if floored
                else (
                    match,
                    limit,
                    offset,
                )
            )
            return conn.execute(
                f"""SELECT s.* FROM stories_fts f
                   JOIN stories s ON s.item_id = f.item_id
                   WHERE stories_fts MATCH ?{score_clause}
                   ORDER BY rank LIMIT ? OFFSET ?""",
                params,
            ).fetchall()
        like = f"%{query.strip()}%"
        score_clause = " AND COALESCE(relevance_score, 0) >= ?" if floored else ""
        params = (
            (like, like, min_score, limit, offset)
            if floored
            else (
                like,
                like,
                limit,
                offset,
            )
        )
        return conn.execute(
            f"""SELECT * FROM stories
               WHERE (title LIKE ? OR summary LIKE ?){score_clause}
               ORDER BY COALESCE(published_date, fetched_at) DESC
               LIMIT ? OFFSET ?""",
            params,
        ).fetchall()

    def count_search(self, query: str, min_score: float = 0.0) -> int:
        """Count full-text search matches (for pagination)."""
        conn = self._get_conn()
        floored = min_score > 0.0
        if self.fts_enabled:
            match = self._fts_query(query)
            if not match:
                return 0
            if floored:
                return conn.execute(
                    """SELECT COUNT(*) FROM stories_fts f
                       JOIN stories s ON s.item_id = f.item_id
                       WHERE stories_fts MATCH ?
                         AND COALESCE(s.relevance_score, 0) >= ?""",
                    (match, min_score),
                ).fetchone()[0]
            return conn.execute(
                "SELECT COUNT(*) FROM stories_fts WHERE stories_fts MATCH ?",
                (match,),
            ).fetchone()[0]
        like = f"%{query.strip()}%"
        score_clause = " AND COALESCE(relevance_score, 0) >= ?" if floored else ""
        params = (like, like, min_score) if floored else (like, like)
        return conn.execute(
            f"SELECT COUNT(*) FROM stories WHERE (title LIKE ? OR summary LIKE ?)"
            f"{score_clause}",
            params,
        ).fetchone()[0]

    def search_stories_filtered(
        self,
        query: str,
        *,
        category: str | None = None,
        state: str | None = None,
        min_score: float = 0.0,
        entity_aliases: list[str] | None = None,
        since: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> list[sqlite3.Row]:
        """Keyword search combined with the structured filters get_stories() accepts.

        Neither :meth:`search_stories` (keyword + score floor only) nor
        :meth:`get_stories` (structured filters, no keyword search) covers a
        parsed natural-language question that combines both — e.g. "show me
        signals above alert grade about star ratings since March" needs a
        keyword match *and* a score/date filter together. This reuses the
        same :meth:`_story_filters` clause-builder and FTS5/LIKE fallback as
        those two, just combined.
        """
        conn = self._get_conn()
        where, params = self._story_filters(
            category, state, min_score, entity_aliases, since=since
        )
        if self.fts_enabled:
            match = self._fts_query(query)
            if not match:
                return []
            combined_where = (
                f"{where} AND stories_fts MATCH ?"
                if where
                else " WHERE stories_fts MATCH ?"
            )
            return conn.execute(
                f"""SELECT s.* FROM stories_fts f
                   JOIN stories s ON s.item_id = f.item_id
                   {combined_where}
                   ORDER BY rank LIMIT ? OFFSET ?""",
                (*params, match, limit, offset),
            ).fetchall()
        like = f"%{query.strip()}%"
        extra = "(title LIKE ? OR summary LIKE ?)"
        combined_where = f"{where} AND {extra}" if where else f" WHERE {extra}"
        return conn.execute(
            f"""SELECT * FROM stories
               {combined_where}
               ORDER BY COALESCE(published_date, fetched_at) DESC
               LIMIT ? OFFSET ?""",
            (*params, like, like, limit, offset),
        ).fetchall()

    def count_search_filtered(
        self,
        query: str,
        *,
        category: str | None = None,
        state: str | None = None,
        min_score: float = 0.0,
        entity_aliases: list[str] | None = None,
        since: str | None = None,
    ) -> int:
        """Count matches for :meth:`search_stories_filtered` (for pagination)."""
        conn = self._get_conn()
        where, params = self._story_filters(
            category, state, min_score, entity_aliases, since=since
        )
        if self.fts_enabled:
            match = self._fts_query(query)
            if not match:
                return 0
            combined_where = (
                f"{where} AND stories_fts MATCH ?"
                if where
                else " WHERE stories_fts MATCH ?"
            )
            return conn.execute(
                f"""SELECT COUNT(*) FROM stories_fts f
                   JOIN stories s ON s.item_id = f.item_id
                   {combined_where}""",
                (*params, match),
            ).fetchone()[0]
        like = f"%{query.strip()}%"
        extra = "(title LIKE ? OR summary LIKE ?)"
        combined_where = f"{where} AND {extra}" if where else f" WHERE {extra}"
        return conn.execute(
            f"SELECT COUNT(*) FROM stories {combined_where}",
            (*params, like, like),
        ).fetchone()[0]

    @staticmethod
    def _story_filters(
        category: str | None,
        state: str | None,
        min_score: float = 0.0,
        entity_aliases: list[str] | None = None,
        source_prefix: str | None = None,
        include_duplicates: bool = False,
        since: str | None = None,
    ) -> tuple[str, list]:
        """Build a shared WHERE clause for story queries.

        ``min_score`` gates the browsable surfaces (feed, topics, states,
        search) so pure source-priority "noise" — items that matched no
        taxonomy keyword and no watched entity — stays out of the public views
        while remaining in the archive. A ``min_score`` of 0.0 (the default)
        adds no clause, so unfiltered callers behave exactly as before.

        ``entity_aliases`` matches stories mentioning ANY of the given watched
        entities (the payer pages pass a canonical group's aliases).
        ``source_prefix`` restricts to sources whose name starts with it (used
        to pull SEC EDGAR filings for a payer).

        ``include_duplicates`` defaults to False, which hides near-duplicate
        stories (``duplicate_of IS NOT NULL``) from the browsable views so the
        same story carried by several sources shows once. Full-archive callers
        (``/status``, ``/health``) pass True.

        ``since`` (inclusive ISO8601 lower bound) windows on
        ``COALESCE(published_date, fetched_at)`` — the same dateless-item
        fallback the ordering uses. The right edge stays unbounded, mirroring
        :meth:`get_daily_counts`: windowed callers drop future-dated rows
        while bucketing instead.
        """
        clauses: list[str] = []
        params: list = []
        if category:
            clauses.append("primary_category = ?")
            params.append(category)
        if state:
            # states is a JSON array of USPS codes; match the quoted token.
            clauses.append("states LIKE ?")
            params.append(f'%"{state}"%')
        if entity_aliases:
            # entities is a JSON array of alias strings; match any quoted token.
            ors = " OR ".join(["entities LIKE ?"] * len(entity_aliases))
            clauses.append(f"({ors})")
            params.extend(f'%"{alias}"%' for alias in entity_aliases)
        if source_prefix:
            clauses.append("source_name LIKE ?")
            params.append(f"{source_prefix}%")
        if since:
            clauses.append("COALESCE(published_date, fetched_at) >= ?")
            params.append(since)
        if min_score > 0.0:
            # NULL scores are treated as 0 so they never slip past the floor.
            clauses.append("COALESCE(relevance_score, 0) >= ?")
            params.append(min_score)
        if not include_duplicates:
            clauses.append("duplicate_of IS NULL")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    def get_stories(
        self,
        category: str | None = None,
        state: str | None = None,
        limit: int = 25,
        offset: int = 0,
        min_score: float = 0.0,
        entity_aliases: list[str] | None = None,
        source_prefix: str | None = None,
        include_duplicates: bool = False,
        since: str | None = None,
    ) -> list[sqlite3.Row]:
        """Return stories in reverse-chronological order, optionally filtered.

        Dateless items (no published_date) fall back to fetched_at so they
        still sort sensibly. ``min_score`` hides sub-floor stories and, by
        default, near-duplicates are hidden too. ``since`` bounds the window
        on the left (see :meth:`_story_filters` for both).
        """
        conn = self._get_conn()
        where, params = self._story_filters(
            category,
            state,
            min_score,
            entity_aliases,
            source_prefix,
            include_duplicates,
            since,
        )
        rows = conn.execute(
            f"""SELECT * FROM stories{where}
                ORDER BY COALESCE(published_date, fetched_at) DESC
                LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()
        return rows

    def count_stories(
        self,
        category: str | None = None,
        state: str | None = None,
        min_score: float = 0.0,
        entity_aliases: list[str] | None = None,
        source_prefix: str | None = None,
        include_duplicates: bool = False,
        since: str | None = None,
    ) -> int:
        """Count stories matching the given filters (for pagination)."""
        conn = self._get_conn()
        where, params = self._story_filters(
            category,
            state,
            min_score,
            entity_aliases,
            source_prefix,
            include_duplicates,
            since,
        )
        row = conn.execute(f"SELECT COUNT(*) FROM stories{where}", params).fetchone()
        return row[0]

    def get_story(self, item_id: str) -> sqlite3.Row | None:
        """Return a single story by id, or None."""
        conn = self._get_conn()
        return conn.execute(
            "SELECT * FROM stories WHERE item_id = ?", (item_id,)
        ).fetchone()

    def recent_story_reps(self, since_days: int) -> list[tuple[str, str]]:
        """Representative stories (``duplicate_of IS NULL``) from the last N days.

        Used by feed near-duplicate detection to point a newly-seen story at an
        already-archived representative when their titles near-match. Returns
        ``[(item_id, title), …]``. Only representatives are returned, so a new
        duplicate always attaches to a root, never to another duplicate.
        """
        if since_days <= 0:
            return []
        conn = self._get_conn()
        cutoff = (datetime.utcnow() - timedelta(days=since_days)).isoformat()
        rows = conn.execute(
            "SELECT item_id, title FROM stories "
            "WHERE duplicate_of IS NULL "
            "AND COALESCE(published_date, fetched_at) >= ?",
            (cutoff,),
        ).fetchall()
        return [(r["item_id"], r["title"]) for r in rows]

    def get_duplicates(self, item_id: str) -> list[sqlite3.Row]:
        """Stories that near-duplicate ``item_id`` (its 'also covered by' group).

        Reverse of ``duplicate_of``: the other sources that carried the same
        story, newest first. Empty when the story is unique.
        """
        conn = self._get_conn()
        return conn.execute(
            "SELECT * FROM stories WHERE duplicate_of = ? "
            "ORDER BY COALESCE(published_date, fetched_at) DESC",
            (item_id,),
        ).fetchall()

    def get_recent_top_stories(
        self,
        since: datetime,
        limit: int = 12,
        min_score: float = 0.0,
        until: datetime | None = None,
    ) -> list[sqlite3.Row]:
        """Return the highest-scoring stories since `since` (for the digest).

        Ordered by relevance score, then recency. Uses
        COALESCE(published_date, fetched_at) so dateless items are still
        windowed sensibly. ``until`` (exclusive) bounds the window on the
        right for callers that need a closed window; the digest leaves it
        unset and stays open-ended. This is the capped digest fetch — the
        Angles page reads every windowed row through
        :meth:`get_recent_story_facets` instead.
        """
        conn = self._get_conn()
        until_clause = ""
        params: list = [since.isoformat()]
        if until is not None:
            until_clause = " AND COALESCE(published_date, fetched_at) < ?"
            params.append(until.isoformat())
        rows = conn.execute(
            f"""SELECT * FROM stories
               WHERE COALESCE(published_date, fetched_at) >= ?{until_clause}
                 AND relevance_score >= ?
                 AND duplicate_of IS NULL
               ORDER BY relevance_score DESC,
                        COALESCE(published_date, fetched_at) DESC
               LIMIT ?""",
            (*params, min_score, limit),
        ).fetchall()
        return rows

    def get_recent_story_facets(
        self,
        since: datetime,
        min_score: float = 0.0,
        until: datetime | None = None,
    ) -> list[sqlite3.Row]:
        """Return every windowed story with just the lens facets, uncapped.

        Feeds the Angles page, which forms cards at lens intersections and so
        needs the exact set of stories in the window, not a truncated top-N.
        Selects only the narrow column set the intersection engine reads
        (title/link/source for display, plus the ``categories``/``entities``/
        ``states`` JSON lenses) — the heavy ``summary``/``public_draft`` blobs
        are skipped. Same windowing and filters as
        :meth:`get_recent_top_stories` (``since`` inclusive, optional ``until``
        exclusive, ``min_score`` floor, representatives only via
        ``duplicate_of IS NULL``). No LIMIT: the windows are time-bounded, so a
        single week's public-story set stays small enough to scan whole.
        """
        conn = self._get_conn()
        until_clause = ""
        params: list = [since.isoformat()]
        if until is not None:
            until_clause = " AND COALESCE(published_date, fetched_at) < ?"
            params.append(until.isoformat())
        rows = conn.execute(
            f"""SELECT item_id, title, link, source_name, published_date,
                       fetched_at, relevance_score, primary_category,
                       categories, entities, states
                  FROM stories
                 WHERE COALESCE(published_date, fetched_at) >= ?{until_clause}
                   AND relevance_score >= ?
                   AND duplicate_of IS NULL
                 ORDER BY relevance_score DESC,
                          COALESCE(published_date, fetched_at) DESC""",
            (*params, min_score),
        ).fetchall()
        return rows

    # --- Reader feedback ---

    def add_feedback(
        self,
        item_id: str,
        verdict: str,
        *,
        channel: str = "local_web",
        voter_key: str | None = None,
        weight: float | None = None,
        suggested_category: str | None = None,
        comment: str | None = None,
        source_ref: str | None = None,
    ) -> None:
        """Append a feedback row for a story.

        `weight` defaults to 1.0 for owner channels and 0.2 for everything else
        unless given explicitly. When `source_ref` is set the insert is
        idempotent (re-ingesting the same crowd reaction is a no-op).
        """
        if verdict not in VALID_VERDICTS:
            raise ValueError(f"Unknown verdict: {verdict!r}")
        if weight is None:
            weight = 1.0 if channel in OWNER_CHANNELS else 0.2
        conn = self._get_conn()
        conn.execute(
            """INSERT OR IGNORE INTO feedback
               (item_id, verdict, channel, voter_key, weight,
                suggested_category, comment, source_ref, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item_id,
                verdict,
                channel,
                voter_key,
                weight,
                suggested_category,
                comment,
                source_ref,
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()

    def get_feedback_summary(self, item_id: str) -> dict:
        """Summarize feedback for one story for the UI.

        Returns verdict counts across all channels plus ``my_verdict`` — the
        most recent owner-channel verdict — so the widget can show prior input.
        """
        conn = self._get_conn()
        counts = {
            row["verdict"]: row["n"]
            for row in conn.execute(
                "SELECT verdict, COUNT(*) AS n FROM feedback "
                "WHERE item_id = ? GROUP BY verdict",
                (item_id,),
            ).fetchall()
        }
        owner = conn.execute(
            "SELECT verdict FROM feedback WHERE item_id = ? AND channel IN "
            "('local_web', 'ntfy', 'cli') ORDER BY id DESC LIMIT 1",
            (item_id,),
        ).fetchone()
        return {"counts": counts, "my_verdict": owner["verdict"] if owner else None}

    def count_feedback(self) -> int:
        """Total number of feedback rows recorded (all channels)."""
        conn = self._get_conn()
        return conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]

    def get_labeled_documents(self) -> list[tuple[str, str]]:
        """Return ``(title + summary, verdict)`` for owner-labeled stories.

        Only owner channels count (ground truth), and only relevant/irrelevant
        verdicts (the labels keyword mining needs). The latest owner verdict per
        story wins, so a correction supersedes an earlier vote.
        """
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT s.title AS title, s.summary AS summary, latest.verdict AS verdict
               FROM stories s
               JOIN (
                   SELECT item_id, verdict,
                          ROW_NUMBER() OVER (
                              PARTITION BY item_id ORDER BY id DESC
                          ) AS rn
                   FROM feedback
                   WHERE channel IN ('local_web', 'ntfy', 'cli')
               ) latest
               ON latest.item_id = s.item_id AND latest.rn = 1
               WHERE latest.verdict IN ('relevant', 'irrelevant')""",
        ).fetchall()
        return [
            (f"{r['title']} {r['summary'] or ''}".strip(), r["verdict"]) for r in rows
        ]

    def get_scored_owner_feedback(self) -> list[dict]:
        """Pair each labeled story's scorer score with its latest owner verdict.

        Joins the story archive (which carries the scorer's ``relevance_score``)
        to the most recent owner-channel verdict per story, keeping only the
        relevance verdicts the disagreement digest reasons about
        (``relevant`` / ``great`` / ``irrelevant``). ``wrong_category`` is a
        categorization signal, not a relevance one, so it is excluded here.
        """
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT s.item_id AS item_id, s.title AS title, s.link AS link,
                      s.source_name AS source, s.relevance_score AS score,
                      latest.verdict AS verdict
               FROM stories s
               JOIN (
                   SELECT item_id, verdict,
                          ROW_NUMBER() OVER (
                              PARTITION BY item_id ORDER BY id DESC
                          ) AS rn
                   FROM feedback
                   WHERE channel IN ('local_web', 'ntfy', 'cli')
               ) latest
               ON latest.item_id = s.item_id AND latest.rn = 1
               WHERE latest.verdict IN ('relevant', 'great', 'irrelevant')""",
        ).fetchall()
        return [
            {
                "item_id": r["item_id"],
                "title": r["title"],
                "link": r["link"],
                "source": r["source"],
                "score": r["score"],
                "verdict": r["verdict"],
            }
            for r in rows
        ]

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

    def get_state_counts(self, min_score: float = 0.0) -> dict[str, int]:
        """Return a {state_code: story_count} map across the archive.

        Each story's `states` JSON array may contain several codes; every code
        is counted. Used by the State Intelligence overview. ``min_score``
        applies the archive floor so state tallies match the filtered feed.
        """
        conn = self._get_conn()
        sql = "SELECT states FROM stories WHERE states IS NOT NULL"
        params: tuple = ()
        if min_score > 0.0:
            sql += " AND COALESCE(relevance_score, 0) >= ?"
            params = (min_score,)
        counts: dict[str, int] = {}
        for row in conn.execute(sql, params):
            for code in json.loads(row[0] or "[]"):
                counts[code] = counts.get(code, 0) + 1
        return counts

    def get_entity_counts(self, min_score: float = 0.0) -> dict[str, int]:
        """Return a {watched_entity_alias: story_count} map across the archive.

        Each story's `entities` JSON array may contain several aliases; every
        alias is counted. Used by the payer intelligence overview (which folds
        aliases into canonical organizations). ``min_score`` applies the
        archive floor so tallies match the filtered feed.
        """
        conn = self._get_conn()
        sql = "SELECT entities FROM stories WHERE entities IS NOT NULL"
        params: tuple = ()
        if min_score > 0.0:
            sql += " AND COALESCE(relevance_score, 0) >= ?"
            params = (min_score,)
        counts: dict[str, int] = {}
        for row in conn.execute(sql, params):
            for alias in json.loads(row[0] or "[]"):
                counts[alias] = counts.get(alias, 0) + 1
        return counts

    def get_entity_stats(
        self, entity_aliases: list[str], min_score: float = 0.0
    ) -> dict:
        """Aggregate category and state footprints for one entity group.

        Scans the stories mentioning any of ``entity_aliases`` once and
        returns ``{"total", "categories": {key: n}, "states": {code: n}}``
        for the payer intelligence page. Small result sets (hundreds of rows
        per payer), so Python-side aggregation is fine.
        """
        conn = self._get_conn()
        where, params = self._story_filters(
            None, None, min_score, entity_aliases=entity_aliases
        )
        categories: dict[str, int] = {}
        states: dict[str, int] = {}
        total = 0
        for row in conn.execute(
            f"SELECT primary_category, states FROM stories{where}", params
        ):
            total += 1
            cat = row["primary_category"] or "uncategorized"
            categories[cat] = categories.get(cat, 0) + 1
            for code in json.loads(row["states"] or "[]"):
                states[code] = states.get(code, 0) + 1
        return {"total": total, "categories": categories, "states": states}

    def get_weekly_counts(
        self,
        weeks: int = 12,
        entity_aliases: list[str] | None = None,
        min_score: float = 0.0,
        now: datetime | None = None,
    ) -> list[dict]:
        """Story volume per week over the last ``weeks`` weeks (oldest→newest).

        Powers the signal-volume sparklines on ``/status`` (all stories) and
        the payer pages (``entity_aliases`` scoping). Buckets on
        ``COALESCE(published_date, fetched_at)`` — the same dateless-item
        fallback the feed uses. Returns ``[{"week_start": iso, "count": n}, …]``
        with every week present (zero-filled), so quiet weeks show as dips.
        """
        from ma_signal_monitor.trends import week_start, weekly_series

        now = now or datetime.utcnow()
        conn = self._get_conn()
        where, params = self._story_filters(
            None, None, min_score, entity_aliases=entity_aliases
        )
        cutoff = (week_start(now) - timedelta(weeks=weeks - 1)).isoformat()
        clause = " AND " if where else " WHERE "
        sql = (
            f"SELECT published_date, fetched_at FROM stories{where}"
            f"{clause}COALESCE(published_date, fetched_at) >= ?"
        )
        dates: list[datetime] = []
        for row in conn.execute(sql, (*params, cutoff)):
            raw = row["published_date"] or row["fetched_at"]
            try:
                dates.append(datetime.fromisoformat(raw))
            except (ValueError, TypeError):
                continue
        series = weekly_series(dates, weeks, now)
        return [{"week_start": s.isoformat(), "count": c} for s, c in series]

    def get_daily_counts(
        self,
        days: int = 30,
        entity_aliases: list[str] | None = None,
        category: str | None = None,
        min_score: float = 0.0,
        now: datetime | None = None,
    ) -> list[dict]:
        """Story volume per day over the last ``days`` days (oldest→newest).

        The daily counterpart to :meth:`get_weekly_counts`, powering the
        per-card "related coverage" timelines. Scope it to one payer via
        ``entity_aliases`` (a canonical group's aliases, matched ANY) or to one
        topic via ``category``; passing both AND-combines them, though the web
        callers pass exactly one. Buckets on
        ``COALESCE(published_date, fetched_at)`` — the same dateless-item
        fallback the feed uses — and hides near-duplicates, so counts line up
        with the deduped feed. Returns ``[{"day": "YYYY-MM-DD", "count": n}, …]``
        with every day present (zero-filled).
        """
        from ma_signal_monitor.trends import daily_series

        now = now or datetime.utcnow()
        conn = self._get_conn()
        where, params = self._story_filters(
            category, None, min_score, entity_aliases=entity_aliases
        )
        cutoff = (now.date() - timedelta(days=days - 1)).isoformat()
        clause = " AND " if where else " WHERE "
        sql = (
            f"SELECT published_date, fetched_at FROM stories{where}"
            f"{clause}COALESCE(published_date, fetched_at) >= ?"
        )
        dates: list[datetime] = []
        for row in conn.execute(sql, (*params, cutoff)):
            raw = row["published_date"] or row["fetched_at"]
            try:
                dates.append(datetime.fromisoformat(raw))
            except (ValueError, TypeError):
                continue
        series = daily_series(dates, days, now)
        return [{"day": d.isoformat(), "count": c} for d, c in series]

    def get_oldest_story_key(
        self,
        *,
        category: str | None = None,
        state: str | None = None,
        entity_aliases: list[str] | None = None,
        min_score: float = 0.0,
    ) -> str | None:
        """Oldest in-scope story's sort key, for the /timeline "all" window (D6).

        Same ``COALESCE(published_date, fetched_at)`` fallback :meth:`get_stories`
        orders by, and the same :meth:`_story_filters` scoping — no ``since``,
        since the point is to find where the archive's window *should* start.
        Returns ``None`` when nothing matches (an empty archive or an empty
        scope) so callers can fall back to a sane default window instead of
        erroring.
        """
        conn = self._get_conn()
        where, params = self._story_filters(category, state, min_score, entity_aliases)
        row = conn.execute(
            f"SELECT MIN(COALESCE(published_date, fetched_at)) AS oldest "
            f"FROM stories{where}",
            params,
        ).fetchone()
        return row["oldest"] if row and row["oldest"] is not None else None

    # --- Delivery Logging ---

    def log_delivery(self, result: DeliveryResult) -> None:
        """Log a delivery attempt."""
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO delivery_log
               (alert_title, success, status_code, error, timestamp, item_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                result.alert_title,
                1 if result.success else 0,
                result.status_code,
                result.error,
                result.timestamp.isoformat(),
                result.item_id,
            ),
        )
        conn.commit()

    def get_alert_delivered(self, item_id: str) -> bool:
        """True if ``item_id`` was ever successfully posted to the webhook.

        Backs the ``ma-signal-feedback alert`` command's sanity check: a
        ``correct``/``false_positive`` verdict only makes sense for a story
        that was actually posted, and ``missed`` only for one that wasn't.
        """
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM delivery_log WHERE item_id = ? AND success = 1 LIMIT 1",
            (item_id,),
        ).fetchone()
        return row is not None

    def get_alert_feedback(self, item_id: str) -> dict | None:
        """Join a story's scoring detail with its alert-outcome feedback.

        Returns the six-factor breakdown, combined score, and the threshold
        in effect when it was scored — the scoring conditions a later
        ``alert_correct``/``alert_false_positive``/``alert_missed`` label
        needs to be traceable to — plus whether it was actually delivered and
        every alert-outcome verdict recorded so far. Read-only reporting: no
        aggregation across stories (that's future work once labels
        accumulate). Returns None if the story isn't archived.
        """
        story = self.get_story(item_id)
        if story is None:
            return None
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT verdict, channel, created_at FROM feedback "
            "WHERE item_id = ? AND verdict IN (?, ?, ?) ORDER BY id DESC",
            (item_id, *ALERT_VERDICTS),
        ).fetchall()
        return {
            "item_id": item_id,
            "title": story["title"],
            "relevance_score": story["relevance_score"],
            "threshold_at_score": story["threshold_at_score"],
            "scoring_breakdown": json.loads(story["scoring_breakdown"] or "[]"),
            "delivered": self.get_alert_delivered(item_id),
            "alert_verdicts": [dict(r) for r in rows],
        }

    def recent_alert_titles(self, since_days: int) -> list[str]:
        """Titles of alerts successfully delivered in the last ``since_days``.

        Used by near-duplicate alert suppression to avoid re-firing a story
        that a prior run already alerted. ``delivery_log`` is retained ~30 days
        (``delivery_log_retention_days``), so a small lookback is always
        available. Returns [] for a non-positive lookback (cross-run check off).
        """
        if since_days <= 0:
            return []
        conn = self._get_conn()
        cutoff = (datetime.utcnow() - timedelta(days=since_days)).isoformat()
        rows = conn.execute(
            "SELECT alert_title FROM delivery_log WHERE success = 1 AND timestamp >= ?",
            (cutoff,),
        ).fetchall()
        return [r["alert_title"] for r in rows]

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

    def get_source_yield(self, min_score: float) -> list[dict]:
        """Per-source relevance yield for the source-review report.

        For each source that has contributed stories, returns the total ingested,
        how many cleared ``min_score``, the resulting yield fraction, mean/max
        score, and last-fetched date. Ordered worst-yield first so low performers
        surface at the top. This is read-only — flagging/disabling is a separate,
        human-confirmed step (see ``source_review``).
        """
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT source_name AS source,
                      COUNT(*) AS total,
                      SUM(CASE WHEN relevance_score >= ? THEN 1 ELSE 0 END) AS relevant,
                      AVG(relevance_score) AS mean_score,
                      MAX(relevance_score) AS max_score,
                      MAX(fetched_at) AS last_fetched
               FROM stories
               GROUP BY source_name""",
            (min_score,),
        ).fetchall()
        stats = []
        for r in rows:
            total = r["total"] or 0
            relevant = r["relevant"] or 0
            stats.append(
                {
                    "source": r["source"],
                    "total": total,
                    "relevant": relevant,
                    "yield": (relevant / total) if total else 0.0,
                    "mean_score": r["mean_score"] or 0.0,
                    "max_score": r["max_score"] or 0.0,
                    "last_fetched": (r["last_fetched"] or "")[:10],
                }
            )
        stats.sort(key=lambda s: (s["yield"], s["max_score"]))
        return stats

    # --- Source Discovery ---

    def upsert_candidate_domain(self, stat) -> None:
        """Accumulate a harvested outbound domain (DomainStat-like object).

        ``times_seen`` and ``relevance_score`` accumulate across runs; the
        example link/story and first-seen are kept from the earliest sighting.
        """
        conn = self._get_conn()
        now = datetime.utcnow().isoformat()
        conn.execute(
            """INSERT INTO candidate_domains
               (domain, times_seen, relevance_score, first_seen_at, last_seen_at,
                example_link, example_story_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(domain) DO UPDATE SET
                   times_seen = times_seen + excluded.times_seen,
                   relevance_score = relevance_score + excluded.relevance_score,
                   last_seen_at = excluded.last_seen_at""",
            (
                stat.domain,
                stat.times_seen,
                stat.relevance_score,
                now,
                now,
                stat.example_link,
                stat.example_story_id,
            ),
        )
        conn.commit()

    def domains_due_for_discovery(
        self,
        limit: int = 20,
        recheck_days: int = 14,
        min_times_seen: int = 2,
    ) -> list[sqlite3.Row]:
        """Top-ranked 'new' domains that haven't been probed recently."""
        conn = self._get_conn()
        cutoff = (datetime.utcnow() - timedelta(days=recheck_days)).isoformat()
        return conn.execute(
            """SELECT * FROM candidate_domains
               WHERE status = 'new'
                 AND times_seen >= ?
                 AND (feeds_checked_at IS NULL OR feeds_checked_at < ?)
               ORDER BY relevance_score DESC
               LIMIT ?""",
            (min_times_seen, cutoff, limit),
        ).fetchall()

    def mark_domain_checked(self, domain: str, checked_at: str | None = None) -> None:
        """Record that a domain was probed for feeds (throttles re-probing)."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE candidate_domains SET feeds_checked_at = ? WHERE domain = ?",
            (checked_at or datetime.utcnow().isoformat(), domain),
        )
        conn.commit()

    def set_domain_status(self, domain: str, status: str) -> None:
        """Update a candidate domain's review status (e.g. 'ignored')."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE candidate_domains SET status = ? WHERE domain = ?",
            (status, domain),
        )
        conn.commit()

    def upsert_candidate_source(
        self,
        feed_url: str,
        domain: str,
        feed_title: str = "",
        discovery_method: str = "",
        times_seen: int = 0,
        relevance_score: float = 0.0,
        status: str = "new",
    ) -> None:
        """Record a discovered feed. On conflict, refresh stats but only upgrade
        the status when it is still 'new' (operator decisions are preserved).
        """
        conn = self._get_conn()
        now = datetime.utcnow().isoformat()
        conn.execute(
            """INSERT INTO candidate_sources
               (feed_url, domain, feed_title, discovery_method, times_seen,
                relevance_score, first_seen_at, last_seen_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(feed_url) DO UPDATE SET
                   feed_title = excluded.feed_title,
                   times_seen = excluded.times_seen,
                   relevance_score = excluded.relevance_score,
                   last_seen_at = excluded.last_seen_at,
                   status = CASE WHEN candidate_sources.status = 'new'
                                 THEN excluded.status
                                 ELSE candidate_sources.status END""",
            (
                feed_url,
                domain,
                feed_title,
                discovery_method,
                times_seen,
                relevance_score,
                now,
                now,
                status,
            ),
        )
        conn.commit()

    def list_candidate_sources(
        self,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[sqlite3.Row]:
        """Candidate feeds ranked by relevance (optionally filtered by status)."""
        conn = self._get_conn()
        where = "WHERE status = ?" if status else ""
        params: list = [status] if status else []
        return conn.execute(
            f"""SELECT * FROM candidate_sources {where}
                ORDER BY relevance_score DESC, last_seen_at DESC
                LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()

    def count_candidate_sources(self, status: str | None = None) -> int:
        """Count candidate feeds (for pagination)."""
        conn = self._get_conn()
        where = "WHERE status = ?" if status else ""
        params: list = [status] if status else []
        return conn.execute(
            f"SELECT COUNT(*) FROM candidate_sources {where}", params
        ).fetchone()[0]

    def get_candidate_source(self, candidate_id: int) -> sqlite3.Row | None:
        """Return a single candidate feed by id, or None."""
        conn = self._get_conn()
        return conn.execute(
            "SELECT * FROM candidate_sources WHERE id = ?", (candidate_id,)
        ).fetchone()

    def set_candidate_status(self, candidate_id: int, status: str) -> None:
        """Set a candidate feed's status (promoted / rejected / new)."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE candidate_sources SET status = ? WHERE id = ?",
            (status, candidate_id),
        )
        conn.commit()

    def get_promoted_sources(self) -> list[sqlite3.Row]:
        """Candidate feeds that should be merged into the live source list."""
        conn = self._get_conn()
        return conn.execute(
            "SELECT feed_url, domain, feed_title FROM candidate_sources "
            "WHERE status IN ('promoted', 'auto_promoted') "
            "ORDER BY relevance_score DESC"
        ).fetchall()

    # --- Cleanup ---

    def cleanup_old_records(
        self,
        seen_retention_days: int = 90,
        log_retention_days: int = 30,
        story_retention_days: int = 365,
        candidate_retention_days: int = 180,
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

        if candidate_retention_days > 0:
            cand_cutoff = (
                datetime.utcnow() - timedelta(days=candidate_retention_days)
            ).isoformat()
            # Prune dormant candidates that were never acted on; keep promoted
            # ones (they back live sources) regardless of age.
            conn.execute(
                "DELETE FROM candidate_sources "
                "WHERE status IN ('new', 'rejected') AND last_seen_at < ?",
                (cand_cutoff,),
            )
            conn.execute(
                "DELETE FROM candidate_domains "
                "WHERE status IN ('new', 'ignored') AND last_seen_at < ?",
                (cand_cutoff,),
            )

        conn.commit()
        logger.info(
            "Cleanup: removed %d old seen items, %d old delivery logs, %d old stories",
            seen_deleted,
            logs_deleted,
            stories_deleted,
        )
        return seen_deleted, logs_deleted, stories_deleted
