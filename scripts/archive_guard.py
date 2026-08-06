#!/usr/bin/env python3
"""Guard the published archive DB against silent data loss in CI.

``deploy-pages.yml`` restores the production ``stories`` archive from the
published GitHub Pages site, ingests into it, and republishes it every run.
That restore has no integrity check: a transient Pages/CDN failure would
previously make the job silently continue with an empty database and then
overwrite the entire accumulated archive with a near-empty one. This script
gives the workflow two checkpoints against that:

    archive_guard.py validate <db_path>
        Exit 0 and print the row count if `db_path` is a usable archive
        (exists, non-empty, passes `PRAGMA integrity_check`, has the core
        table). Exit 1 with a reason on stderr otherwise. Used right after
        the restore download, before the job trusts the file.

    archive_guard.py rowcount <db_path>
        Print the core table's row count, or 0 if the file/table is absent.
        Always exits 0 — unlike `validate`, "nothing there" isn't an error
        (a genuine cold start has no DB yet).

    archive_guard.py compare --before N --after <db_path>
        Exit 1 if the archive shrank catastrophically between `before` (a
        row count captured earlier in the run) and the current count in
        `after`. Used right before the build step overwrites the published
        DB, as the last line of defense against publishing a truncated
        archive.

Deliberately dependency-free (stdlib only) so it needs nothing beyond the
Python already on the runner.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

# The browsable story archive (see SCHEMA_SQL in src/ma_signal_monitor/
# storage.py) — the table the published site and this whole guard exist to
# protect. Keep in sync with storage.py if that table is ever renamed.
CORE_TABLE = "stories"

# storage.cleanup_old_records() is the only code in this repo that deletes
# from `stories` — it prunes rows older than config.story_retention_days
# (config/app.yaml, default 365) and runs at the end of *every* pipeline run
# (see main.py). That's real, legitimate shrinkage, but it's bounded: on the
# deploy-pages ~2-hour cadence, a single run can only prune the sliver of the
# archive that crossed the retention cutoff since the last run, not the bulk
# of it. A catastrophic loss (the archive silently replaced by an empty DB)
# drops the row count by an order of magnitude in one run. This tolerance
# sits comfortably inside that gap: ordinary retention pruning should never
# approach it, so any run tripping it is real data loss, not the cleanup job
# working as intended.
#
# Note: `ma-signal-backfill --rescore` (main.py's "Backfill categorization"
# step) rewrites relevance_score/categories/entities in place via UPDATE — it
# never touches row counts, so it's invisible to (and doesn't need special-
# casing by) this comparison.
ROW_SHRINK_TOLERANCE = 0.05  # allow up to 5% shrinkage per run


def _row_count(db_path: Path) -> int:
    """Row count of CORE_TABLE, or 0 if the file/table doesn't exist.

    Deliberately lenient — mirrors the `rowcount` subcommand's semantics.
    `_validate` below is the strict, error-reporting counterpart used where
    "missing" must be treated as a failure instead of a cold start.
    """
    if not db_path.exists() or db_path.stat().st_size == 0:
        return 0
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if CORE_TABLE not in tables:
                return 0
            return conn.execute(f"SELECT COUNT(*) FROM {CORE_TABLE}").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def _validate(db_path: Path) -> tuple[bool, int, str]:
    """Check that `db_path` is a usable SQLite archive with the core table.

    Returns (ok, row_count, message). `row_count` is 0 when `ok` is False.
    """
    if not db_path.exists():
        return False, 0, f"{db_path} does not exist"
    if db_path.stat().st_size == 0:
        return False, 0, f"{db_path} is empty (0 bytes)"
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            check = conn.execute("PRAGMA integrity_check").fetchone()
            if check is None or check[0] != "ok":
                return False, 0, f"PRAGMA integrity_check failed: {check}"
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if CORE_TABLE not in tables:
                return False, 0, f"expected table '{CORE_TABLE}' is missing"
            count = conn.execute(f"SELECT COUNT(*) FROM {CORE_TABLE}").fetchone()[0]
            return True, count, "ok"
        finally:
            conn.close()
    except sqlite3.Error as e:
        return False, 0, f"not a usable SQLite database: {e}"


def cmd_validate(args: argparse.Namespace) -> int:
    ok, count, message = _validate(Path(args.db_path))
    if not ok:
        print(f"INVALID archive at {args.db_path}: {message}", file=sys.stderr)
        return 1
    print(count)
    return 0


def cmd_rowcount(args: argparse.Namespace) -> int:
    print(_row_count(Path(args.db_path)))
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    before = args.before
    after = _row_count(Path(args.after))
    if before <= 0:
        # Nothing existed before this run (cold start) — any result is fine.
        print(f"before={before} after={after}: cold start, nothing to compare.")
        return 0
    min_allowed = before * (1 - ROW_SHRINK_TOLERANCE)
    if after < min_allowed:
        loss_pct = (1 - after / before) * 100
        print(
            f"CATASTROPHIC SHRINK: '{CORE_TABLE}' row count dropped from "
            f"{before} to {after} ({loss_pct:.1f}% loss), beyond the "
            f"{ROW_SHRINK_TOLERANCE:.0%} tolerance for legitimate retention "
            "pruning. Refusing to publish.",
            file=sys.stderr,
        )
        return 1
    print(f"before={before} after={after}: within tolerance.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate and compare the archive DB to guard against "
        "publishing a truncated or corrupt production archive."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_validate = subparsers.add_parser(
        "validate", help="Check that a DB file is a usable archive."
    )
    p_validate.add_argument("db_path", help="Path to the SQLite DB to validate.")
    p_validate.set_defaults(func=cmd_validate)

    p_rowcount = subparsers.add_parser(
        "rowcount", help="Print the core table's row count (0 if absent)."
    )
    p_rowcount.add_argument("db_path", help="Path to the SQLite DB to inspect.")
    p_rowcount.set_defaults(func=cmd_rowcount)

    p_compare = subparsers.add_parser(
        "compare", help="Fail if the archive shrank catastrophically."
    )
    p_compare.add_argument(
        "--before", type=int, required=True, help="Row count before this run."
    )
    p_compare.add_argument(
        "--after", required=True, help="Path to the SQLite DB to check after."
    )
    p_compare.set_defaults(func=cmd_compare)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
