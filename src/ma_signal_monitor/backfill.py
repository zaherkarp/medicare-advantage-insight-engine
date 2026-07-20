"""Reclassification backfill for the story archive (``ma-signal-backfill``).

``config/taxonomy.yaml`` evolves over time — categories get added, keywords
get pruned or reweighted, and the MA-context gate (``scoring._has_ma_context``)
was introduced well after the first archive rows were written. Classification
happens exactly once per story, at ingest (``main.py:_persist_stories`` calls
``classify.classify_item`` before ``store.upsert_story``), so a row scored
under an old taxonomy keeps its stale ``primary_category`` forever unless
something revisits it. This module is that something: it re-runs scoring and
classification for every archived row under *today's* config and writes back
whatever changed, including near-duplicate rows (``duplicate_of`` set) — they
surface on story pages as "also covered by" and deserve correct categories
too, even though the main feed hides them.

Re-scoring a stored row is faithful to the original ingest because any
context a fetcher injects ahead of scoring (e.g. ``fetchers/litigation.py``
prepending a tracker feed's guaranteed topic to each entry's boilerplate
summary) was folded into ``summary`` *before* persistence — so the stored
``title``/``summary`` alone reconstruct exactly what the scorer originally
saw.

By default only the category fields (``primary_category``, ``categories``)
are rewritten. ``relevance_score``/``entities``/``states`` are left untouched
unless ``--rescore`` is passed, because rewriting a score can silently move a
story across ``archive_min_score`` or a digest visibility threshold — a much
bigger behavior change than "this story is filed under the right topic now",
and one an operator should opt into deliberately (after checking a
``--dry-run`` report), not have fired as a side effect of a taxonomy edit.

Some rows will legitimately stay ``uncategorized`` after a re-run: the
MA-context gate intentionally withholds category-keyword credit from broad,
low-priority sources that never establish real Medicare/MA context in the
text. Those rows are counted separately as ``gated`` in the report so they
read as "working as intended" rather than as backfill failures to chase.
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from ma_signal_monitor.classify import classify_item
from ma_signal_monitor.config import AppConfig, load_config
from ma_signal_monitor.geo import detect_states
from ma_signal_monitor.models import NormalizedItem
from ma_signal_monitor.scoring import score_item
from ma_signal_monitor.storage import StateStore

# The `factor` scoring.score_item leaves on a ScoredItem's reasons when the
# MA-context gate suppressed keyword scoring for a broad, low-priority source
# (see scoring.py:_has_ma_context / score_item). Matched on `factor`, not the
# human-readable `detail` string, so wording tweaks there can't silently break
# gate detection here.
_GATE_FACTOR = "ma_context_gate"

# Commit every this-many written rows so a large archive doesn't sit inside
# one giant uncommitted transaction. update_story_classification/_scoring
# deliberately don't self-commit (see storage.py) so this module controls the
# batching.
_COMMIT_BATCH = 500


def _row_to_item(row) -> NormalizedItem:
    """Rebuild a NormalizedItem from an archived ``stories`` row.

    Only the fields scoring/classification actually read need to be faithful
    (``title``, ``summary``, ``source_priority``, ``source_name``);
    ``source_type``/``source_tags``/``author`` aren't columns on the row and
    play no part in scoring, so they get inert placeholders — the same
    reconstruction shortcut ``scripts/scorecard.py`` uses for its golden-set
    items. Dates are parsed None-safe: ``published_date`` may genuinely be
    NULL (dateless items), and while ``fetched_at`` is NOT NULL in the schema,
    a defensive fallback keeps this robust against a stray blank value.
    """
    published_date = (
        datetime.fromisoformat(row["published_date"]) if row["published_date"] else None
    )
    fetched_at = (
        datetime.fromisoformat(row["fetched_at"])
        if row["fetched_at"]
        else datetime.utcnow()
    )
    return NormalizedItem(
        item_id=row["item_id"],
        source_name=row["source_name"],
        source_type="rss",
        source_priority=row["source_priority"] or 3,
        source_tags=[],
        title=row["title"],
        link=row["link"],
        published_date=published_date,
        summary=row["summary"] or "",
        fetched_at=fetched_at,
    )


def backfill_categories(
    store: StateStore,
    config: AppConfig,
    *,
    rescore: bool = False,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict:
    """Re-run scoring + classification for every archived story.

    Iterates every row in ``stories`` — including near-duplicates
    (``duplicate_of IS NOT NULL``) — via a plain SELECT rather than the
    story-archive read helpers (:meth:`StateStore.get_stories` and friends
    hide duplicates by default). For each row this rebuilds a NormalizedItem,
    re-scores and re-classifies it under ``config``, and compares the result
    against the stored ``primary_category``/``categories`` (a NULL stored
    category normalizes to ``"uncategorized"``; category *lists* compare
    order-insensitively so a taxonomy reordering alone isn't reported as a
    change). A row whose categorization changed is written back via
    :meth:`StateStore.update_story_classification` — unless ``dry_run`` — and,
    when ``rescore`` is set, also gets ``relevance_score``/``entities``/
    ``states`` refreshed from the same fresh ScoredItem via
    :meth:`StateStore.update_story_scoring`. Rows whose categorization is
    unchanged are never rescored either, keeping ``--rescore`` scoped to the
    rows this pass is already touching rather than a full archive-wide
    rescore. ``limit`` caps how many rows are examined (mainly for
    smoke-testing against a large archive).

    Returns a report dict:
      - ``total``: rows examined.
      - ``changed`` / ``unchanged``: categorization outcome counts.
      - ``uncategorized_to_cat``: rows that went from uncategorized to a real
        category.
      - ``cat_to_uncategorized``: rows that went the other way (usually a
        keyword pruned from the taxonomy, or the MA-context gate newly
        applying).
      - ``gated``: rows where the MA-context gate suppressed keyword scoring
        on this pass (independent of whether categorization changed — a
        gated row that was already uncategorized stays "unchanged" but still
        counts here).
      - ``transitions``: ``{(old_category, new_category): count}`` across all
        changed rows, for the report's top-transitions line.
    """
    conn = store._get_conn()
    sql = "SELECT * FROM stories ORDER BY rowid"
    if limit is not None:
        sql += " LIMIT ?"
        rows = conn.execute(sql, (limit,)).fetchall()
    else:
        rows = conn.execute(sql).fetchall()

    report: dict = {
        "total": 0,
        "changed": 0,
        "unchanged": 0,
        "uncategorized_to_cat": 0,
        "cat_to_uncategorized": 0,
        "gated": 0,
        "transitions": {},
    }

    pending_commits = 0
    for row in rows:
        report["total"] += 1
        item = _row_to_item(row)
        scored = score_item(item, config)
        new_category = classify_item(scored, config)
        new_categories = scored.matched_categories

        if any(reason.factor == _GATE_FACTOR for reason in scored.reasons):
            report["gated"] += 1

        old_category = row["primary_category"] or "uncategorized"
        old_categories = json.loads(row["categories"] or "[]")
        changed = old_category != new_category or sorted(old_categories) != sorted(
            new_categories
        )

        if not changed:
            report["unchanged"] += 1
            continue

        report["changed"] += 1
        transition = (old_category, new_category)
        report["transitions"][transition] = report["transitions"].get(transition, 0) + 1
        if old_category == "uncategorized" and new_category != "uncategorized":
            report["uncategorized_to_cat"] += 1
        elif old_category != "uncategorized" and new_category == "uncategorized":
            report["cat_to_uncategorized"] += 1

        if dry_run:
            continue

        store.update_story_classification(item.item_id, new_category, new_categories)
        if rescore:
            store.update_story_scoring(
                item.item_id,
                scored.relevance_score,
                scored.matched_entities,
                detect_states(scored),
            )

        pending_commits += 1
        if pending_commits >= _COMMIT_BATCH:
            conn.commit()
            pending_commits = 0

    if not dry_run:
        conn.commit()

    return report


def _format_report(report: dict) -> str:
    """Render a backfill report dict as the CLI's human-readable summary."""
    lines = [
        f"Examined {report['total']} stories: {report['changed']} changed, "
        f"{report['unchanged']} unchanged "
        f"({report['uncategorized_to_cat']} uncategorized -> categorized, "
        f"{report['cat_to_uncategorized']} categorized -> uncategorized).",
    ]
    if report["transitions"]:
        lines.append("Top transitions:")
        ranked = sorted(
            report["transitions"].items(), key=lambda kv: kv[1], reverse=True
        )
        for (old, new), count in ranked[:15]:
            lines.append(f"  {old} -> {new}: {count}")
    lines.append(
        f"gated (legitimately uncategorized broad-source rows): {report['gated']}"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (installed as ``ma-signal-backfill``).

    Usage: ``ma-signal-backfill [--root PATH] [--dry-run] [--rescore] [--limit N]``

    Idempotent by design — a taxonomy that hasn't changed since the last run
    reports zero changes — so it is safe to run unconditionally on every CI
    build (see ``.github/workflows/deploy-pages.yml``), self-healing the
    archive whenever ``config/taxonomy.yaml`` is edited.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Reclassify the story archive under the current taxonomy. "
            "Rewrites primary_category/categories by default; --rescore also "
            "refreshes relevance_score/entities/states on changed rows."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root containing config/ and the archive DB (default: cwd)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing anything",
    )
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="Also refresh relevance_score/entities/states on changed rows",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only examine the first N rows (mostly for smoke-testing)",
    )
    args = parser.parse_args(argv)

    config = load_config(args.root)
    store = StateStore(args.root / config.db_path)
    try:
        report = backfill_categories(
            store,
            config,
            rescore=args.rescore,
            dry_run=args.dry_run,
            limit=args.limit,
        )
    finally:
        store.close()

    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"{prefix}{_format_report(report)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
