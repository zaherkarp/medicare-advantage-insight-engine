#!/usr/bin/env python3
"""Ask a plain-language question over the story archive.

Usage:
    python scripts/ask_archive.py "show me everything above alert grade related to measure set changes since March"
    python scripts/ask_archive.py --root /path/to/project --limit 10 "Humana stories in Texas this month"

Translates the question into the same structured filters the web app already
supports (category, score tier, date range, watched entity, state, keyword
search) via ``ma_signal_monitor.query_parser``, then reads the archive
read-only — this never touches scoring, thresholds, or the ingestion
pipeline. See docs/feedback.md and query_parser.py for how phrases are
recognized.
"""

import argparse
import sys
from pathlib import Path

# Add src to path for direct script execution
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ma_signal_monitor.config import load_config
from ma_signal_monitor.query_parser import parse_query
from ma_signal_monitor.storage import StateStore


def _run_query(store: StateStore, parsed, limit: int, offset: int = 0):
    """Execute a ParsedQuery against the archive. Returns (rows, total)."""
    if parsed.keywords:
        rows = store.search_stories_filtered(
            parsed.keywords,
            category=parsed.category,
            state=parsed.state,
            min_score=parsed.min_score,
            entity_aliases=parsed.entity_aliases or None,
            since=parsed.since,
            limit=limit,
            offset=offset,
        )
        total = store.count_search_filtered(
            parsed.keywords,
            category=parsed.category,
            state=parsed.state,
            min_score=parsed.min_score,
            entity_aliases=parsed.entity_aliases or None,
            since=parsed.since,
        )
    else:
        rows = store.get_stories(
            category=parsed.category,
            state=parsed.state,
            min_score=parsed.min_score,
            entity_aliases=parsed.entity_aliases or None,
            since=parsed.since,
            limit=limit,
            offset=offset,
        )
        total = store.count_stories(
            category=parsed.category,
            state=parsed.state,
            min_score=parsed.min_score,
            entity_aliases=parsed.entity_aliases or None,
            since=parsed.since,
        )
    return rows, total


def _format_row(row) -> str:
    date = (row["published_date"] or row["fetched_at"] or "")[:10]
    score = row["relevance_score"] or 0.0
    category = (row["primary_category"] or "uncategorized")[:20]
    source = (row["source_name"] or "")[:20]
    return f"{date}  {score:.2f}  {category:<20}  {source:<20}  {row['title'][:70]}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ask a plain-language question over the story archive (read-only)."
    )
    parser.add_argument("question", nargs="+", help='The question, e.g. "..."')
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root containing config/ and the archive DB (default: cwd)",
    )
    parser.add_argument(
        "--limit", type=int, default=20, help="Max rows to print (default: 20)"
    )
    args = parser.parse_args(argv)
    question = " ".join(args.question)

    config = load_config(args.root)
    store = StateStore(args.root / config.db_path)
    try:
        parsed = parse_query(question, config)
        print(f"Parsed: {'; '.join(parsed.notes)}")

        rows, total = _run_query(store, parsed, args.limit)
        print(f"{total} match{'es' if total != 1 else ''} (showing {len(rows)}):")
        for row in rows:
            print(_format_row(row))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
