"""Main orchestrator for MA Signal Monitor.

This is the primary entry point. It coordinates the full pipeline:
fetch → normalize → dedupe → score → classify → draft → deliver → persist.
"""

import logging
import sys
from pathlib import Path

from ma_signal_monitor.config import AppConfig, load_config
from ma_signal_monitor.dedupe import filter_new_items, mark_items_seen
from ma_signal_monitor.delivery import deliver_alerts
from ma_signal_monitor.drafting import draft_alerts
from ma_signal_monitor.fetchers.cms import fetch_cms
from ma_signal_monitor.fetchers.rss import fetch_rss
from ma_signal_monitor.fetchers.sec import fetch_sec
from ma_signal_monitor.logging_setup import setup_logging
from ma_signal_monitor.models import RawFeedItem
from ma_signal_monitor.normalize import normalize_items
from ma_signal_monitor.scoring import score_items
from ma_signal_monitor.storage import StateStore

logger = logging.getLogger("ma_signal_monitor.main")

# Dispatch fetchers by source type
_FETCHERS = {
    "rss": fetch_rss,
    "sec": fetch_sec,
    "cms": fetch_cms,
}


def _fetch_all_sources(config: AppConfig) -> list[RawFeedItem]:
    """Fetch items from all enabled sources, handling errors per-source."""
    all_items: list[RawFeedItem] = []
    enabled_sources = [s for s in config.sources if s.enabled]

    for source in enabled_sources:
        fetcher = _FETCHERS.get(source.type)
        if not fetcher:
            logger.warning(
                "Unknown source type '%s' for '%s', skipping", source.type, source.name
            )
            continue

        try:
            items = fetcher(
                source,
                timeout=config.request_timeout,
                user_agent=config.user_agent,
                max_items=config.max_items_per_source,
            )
            all_items.extend(items)
        except Exception as e:
            logger.error("Error fetching '%s': %s", source.name, e)
            # Continue with other sources — one bad feed shouldn't stop the run

    logger.info(
        "Fetched %d total items from %d sources", len(all_items), len(enabled_sources)
    )
    return all_items


def run(
    config: AppConfig | None = None, project_root: str | Path | None = None
) -> dict:
    """Execute the full monitoring pipeline.

    Args:
        config: Optional pre-loaded config. If None, loads from project_root.
        project_root: Root directory of the project. Defaults to cwd.

    Returns:
        A summary dict with counts of items processed, alerts sent, etc.
    """
    root = Path(project_root) if project_root else Path.cwd()

    if config is None:
        config = load_config(root)

    setup_logging(config.log_level, str(root / "logs"))

    logger.info("=== MA Signal Monitor run starting ===")

    # Initialize storage
    db_path = root / config.db_path
    store = StateStore(db_path)
    run_id = store.start_run()

    summary = {
        "items_fetched": 0,
        "items_new": 0,
        "items_relevant": 0,
        "alerts_sent": 0,
        "alerts_failed": 0,
        "errors": 0,
    }

    try:
        # 1. Fetch
        raw_items = _fetch_all_sources(config)
        summary["items_fetched"] = len(raw_items)

        if not raw_items:
            logger.warning("No items fetched from any source")
            store.end_run(run_id, notes="No items fetched")
            return summary

        # 2. Normalize
        normalized = normalize_items(raw_items, config.max_summary_length)

        # 3. Deduplicate
        new_items = filter_new_items(normalized, store)
        summary["items_new"] = len(new_items)

        if not new_items:
            logger.info("No new items after deduplication")
            store.end_run(
                run_id,
                items_fetched=summary["items_fetched"],
                notes="All items were duplicates",
            )
            # Still mark all normalized items as seen (in case they were new
            # but just had no new content)
            mark_items_seen(normalized, store)
            return summary

        # 4. Score
        scored = score_items(new_items, config)
        relevant = [
            s for s in scored if s.relevance_score >= config.min_relevance_score
        ]
        summary["items_relevant"] = len(relevant)

        # 5. Draft alerts
        alerts = draft_alerts(scored, config)

        # 6. Deliver
        if alerts:
            results = deliver_alerts(alerts, config)
            for result in results:
                store.log_delivery(result)
                if result.success:
                    summary["alerts_sent"] += 1
                else:
                    summary["alerts_failed"] += 1
        else:
            logger.info("No alerts to deliver (none met relevance threshold)")

        # 7. Mark items as seen (all new items, not just relevant ones)
        mark_items_seen(new_items, store)

        # 8. Cleanup old records
        store.cleanup_old_records(
            config.seen_item_retention_days,
            config.delivery_log_retention_days,
        )

    except Exception as e:
        logger.exception("Pipeline error: %s", e)
        summary["errors"] += 1
    finally:
        store.end_run(
            run_id,
            items_fetched=summary["items_fetched"],
            items_new=summary["items_new"],
            items_relevant=summary["items_relevant"],
            alerts_sent=summary["alerts_sent"],
            errors=summary["errors"] + summary["alerts_failed"],
        )
        store.close()

    logger.info(
        "=== Run complete: fetched=%d, new=%d, relevant=%d, sent=%d, failed=%d ===",
        summary["items_fetched"],
        summary["items_new"],
        summary["items_relevant"],
        summary["alerts_sent"],
        summary["alerts_failed"],
    )
    return summary


def main():
    """CLI entry point."""
    project_root = Path.cwd()

    # Allow passing project root as argument
    if len(sys.argv) > 1:
        project_root = Path(sys.argv[1])

    try:
        summary = run(project_root=project_root)
        print(f"\nRun summary: {summary}")
    except FileNotFoundError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(130)


if __name__ == "__main__":
    main()
