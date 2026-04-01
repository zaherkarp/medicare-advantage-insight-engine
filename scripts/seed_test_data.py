#!/usr/bin/env python3
"""Seed the system with test data for development and QA.

Creates sample feed items and runs them through the pipeline to verify
end-to-end behavior without needing live RSS feeds.

Usage:
    python scripts/seed_test_data.py [--project-root /path/to/project]
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add src to path for direct script execution
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ma_signal_monitor.config import load_config
from ma_signal_monitor.dedupe import filter_new_items, mark_items_seen
from ma_signal_monitor.delivery import deliver_alerts
from ma_signal_monitor.drafting import draft_alerts
from ma_signal_monitor.logging_setup import setup_logging
from ma_signal_monitor.models import RawFeedItem
from ma_signal_monitor.normalize import normalize_items
from ma_signal_monitor.scoring import score_items
from ma_signal_monitor.storage import StateStore

SAMPLE_ITEMS = [
    RawFeedItem(
        source_name="Test Source",
        source_type="rss",
        source_url="https://example.com/feed",
        source_priority=4,
        source_tags=["test"],
        title="UnitedHealthcare expands Medicare Advantage service area to 15 new counties",
        link="https://example.com/article/uhc-expansion",
        published=(datetime.utcnow() - timedelta(hours=6)).strftime(
            "%a, %d %b %Y %H:%M:%S +0000"
        ),
        summary=(
            "UnitedHealthcare announced plans to expand its Medicare Advantage service area "
            "to include 15 additional counties across three states, signaling continued "
            "enrollment growth strategy heading into the annual enrollment period."
        ),
    ),
    RawFeedItem(
        source_name="Test Source",
        source_type="rss",
        source_url="https://example.com/feed",
        source_priority=5,
        source_tags=["test", "cms"],
        title="CMS proposes new Star Ratings methodology changes for 2027",
        link="https://example.com/article/cms-stars-2027",
        published=(datetime.utcnow() - timedelta(hours=12)).strftime(
            "%a, %d %b %Y %H:%M:%S +0000"
        ),
        summary=(
            "The Centers for Medicare & Medicaid Services released a proposed rule "
            "outlining significant changes to the Medicare Advantage Star Ratings "
            "methodology, including adjustments to quality measure weights and the "
            "introduction of new patient experience metrics effective 2027."
        ),
    ),
    RawFeedItem(
        source_name="Test Source",
        source_type="rss",
        source_url="https://example.com/feed",
        source_priority=3,
        source_tags=["test", "financial"],
        title="Humana reports rising medical loss ratio amid cost pressure",
        link="https://example.com/article/humana-mlr",
        published=(datetime.utcnow() - timedelta(hours=24)).strftime(
            "%a, %d %b %Y %H:%M:%S +0000"
        ),
        summary=(
            "Humana's latest quarterly results show a rising medical loss ratio driven "
            "by increased utilization in its Medicare Advantage plans. The company noted "
            "margin pressure from higher-than-expected inpatient costs and signaled "
            "potential benefit adjustments for the upcoming plan year."
        ),
    ),
    RawFeedItem(
        source_name="Test Source",
        source_type="rss",
        source_url="https://example.com/feed",
        source_priority=2,
        source_tags=["test"],
        title="Local hospital adds new cafeteria menu options",
        link="https://example.com/article/cafeteria",
        published=(datetime.utcnow() - timedelta(hours=2)).strftime(
            "%a, %d %b %Y %H:%M:%S +0000"
        ),
        summary=(
            "Springfield General Hospital announced new cafeteria menu options "
            "for staff and visitors, including expanded vegetarian selections."
        ),
    ),
    RawFeedItem(
        source_name="Test Source",
        source_type="rss",
        source_url="https://example.com/feed",
        source_priority=4,
        source_tags=["test", "strategy"],
        title="Aetna partners with Oak Street Health to expand value-based primary care for Medicare Advantage members",
        link="https://example.com/article/aetna-oak-street",
        published=(datetime.utcnow() - timedelta(hours=8)).strftime(
            "%a, %d %b %Y %H:%M:%S +0000"
        ),
        summary=(
            "CVS Health's Aetna Medicare division announced a new partnership with "
            "Oak Street Health to expand access to value-based primary care for "
            "its Medicare Advantage members in select markets, part of a broader "
            "vertical integration strategy in care delivery."
        ),
    ),
]


def main():
    parser = argparse.ArgumentParser(description="Seed test data")
    parser.add_argument(
        "--project-root",
        type=str,
        default=str(Path(__file__).resolve().parent.parent),
    )
    parser.add_argument(
        "--deliver",
        action="store_true",
        help="Actually deliver alerts to webhook (default: skip delivery)",
    )
    args = parser.parse_args()

    root = Path(args.project_root)

    try:
        config = load_config(root)
    except (FileNotFoundError, ValueError) as e:
        print(f"Config error: {e}", file=sys.stderr)
        print("Tip: Copy .env.example to .env and set WEBHOOK_URL", file=sys.stderr)
        sys.exit(1)

    setup_logging(config.log_level)

    print(f"Using {len(SAMPLE_ITEMS)} sample items")

    # Normalize
    normalized = normalize_items(SAMPLE_ITEMS, config.max_summary_length)
    print(f"Normalized: {len(normalized)} items")

    # Score
    scored = score_items(normalized, config)
    print("\nScoring results:")
    for s in scored:
        marker = ">>>" if s.relevance_score >= config.min_relevance_score else "   "
        print(f"  {marker} [{s.relevance_score:.3f}] {s.item.title[:70]}")
        for r in s.reasons[:3]:
            print(f"        {r.factor}: {r.detail} (+{r.contribution:.3f})")

    # Draft alerts
    alerts = draft_alerts(scored, config)
    print(f"\nDrafted {len(alerts)} alerts (threshold: {config.min_relevance_score})")

    for alert in alerts:
        print(f"\n  --- Alert: {alert.internal.title[:60]} ---")
        print(f"  Category: {alert.internal.trigger_category}")
        print(f"  Confidence: {alert.internal.confidence}")
        print(f"  Entities: {alert.internal.entities}")
        print(f"  Why: {alert.internal.why_it_matters[:100]}")

    # Optionally deliver
    if args.deliver and alerts:
        print(f"\nDelivering {len(alerts)} alerts to {config.webhook_url}...")
        results = deliver_alerts(alerts, config)
        for r in results:
            status = "OK" if r.success else f"FAIL ({r.error})"
            print(f"  {r.alert_title[:60]}: {status}")
    elif alerts:
        print(f"\nSkipping delivery (use --deliver to send to {config.webhook_url})")

    # Dedupe check
    store = StateStore(root / config.db_path)
    new_before = filter_new_items(normalized, store)
    print(f"\nDedupe check: {len(new_before)} would be new items")
    mark_items_seen(normalized, store)
    new_after = filter_new_items(normalized, store)
    print(f"After marking seen: {len(new_after)} new items (should be 0)")
    store.close()


if __name__ == "__main__":
    main()
