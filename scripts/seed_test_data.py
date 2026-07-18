#!/usr/bin/env python3
"""Seed the system with test data for development and QA.

Creates sample feed items and runs them through the pipeline to verify
end-to-end behavior without needing live RSS feeds.

Usage:
    python scripts/seed_test_data.py [--project-root /path/to/project]
"""

import argparse
import sys
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

# Add src to path for direct script execution
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ma_signal_monitor.classify import classify_item
from ma_signal_monitor.config import load_config
from ma_signal_monitor.dedupe import filter_new_items, mark_items_seen
from ma_signal_monitor.delivery import deliver_alerts
from ma_signal_monitor.drafting import draft_alerts
from ma_signal_monitor.geo import detect_states
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
    # --- Intersection-engineered samples (drive the Angles lens overlaps) ---
    # Two Humana stories that each hit financial + membership keywords and name
    # Florida, so the payer×topic, topic×topic, and topic×state lenses all
    # overlap (min 2 stories per Angles card).
    RawFeedItem(
        source_name="Test Source",
        source_type="rss",
        source_url="https://example.com/feed",
        source_priority=4,
        source_tags=["test", "financial", "membership"],
        title="Humana projects Medicare Advantage membership decline in Florida amid margin pressure",
        link="https://example.com/article/humana-florida-membership",
        published=(datetime.utcnow() - timedelta(hours=18)).strftime(
            "%a, %d %b %Y %H:%M:%S +0000"
        ),
        summary=(
            "Humana said its Medicare Advantage membership in Florida could fall next "
            "year as the insurer prunes plans under margin pressure, citing higher "
            "medical costs and rising utilization across the state. Management flagged "
            "benefit reductions and a narrower service area heading into open enrollment."
        ),
    ),
    RawFeedItem(
        source_name="Test Source",
        source_type="rss",
        source_url="https://example.com/feed",
        source_priority=3,
        source_tags=["test", "financial", "membership"],
        title="Humana narrows Medicare Advantage service area in Florida as medical loss ratio climbs",
        link="https://example.com/article/humana-florida-mlr",
        published=(datetime.utcnow() - timedelta(hours=30)).strftime(
            "%a, %d %b %Y %H:%M:%S +0000"
        ),
        summary=(
            "Humana will exit several Florida counties and trim enrollment targets for "
            "its Medicare Advantage plans, pointing to a rising medical loss ratio and "
            "sustained cost trend pressure. The company expects membership losses and "
            "lower revenue in the region next plan year."
        ),
    ),
    # A Humana + UnitedHealthcare co-mention themed on policy + financial, so the
    # payer×payer lens has a data point and the policy→financial causal edge picks
    # up a second qualifying story (pairs with the CMS rate-notice item below).
    RawFeedItem(
        source_name="Test Source",
        source_type="rss",
        source_url="https://example.com/feed",
        source_priority=4,
        source_tags=["test", "policy", "financial"],
        title="Humana and UnitedHealthcare warn CMS rate notice will squeeze Medicare Advantage margins",
        link="https://example.com/article/humana-uhc-rate-notice",
        published=(datetime.utcnow() - timedelta(hours=20)).strftime(
            "%a, %d %b %Y %H:%M:%S +0000"
        ),
        summary=(
            "Humana and UnitedHealthcare both cautioned that the latest CMS rate notice "
            "and benchmark update will pressure Medicare Advantage margins in the coming "
            "plan year, adding to the cost trend and utilization concerns already "
            "weighing on earnings."
        ),
    ),
    # A single item that matches BOTH policy (CMS / rate notice) and financial
    # (margins / benefit reductions) keywords, so a genuine causal-chain card
    # (policy → financial) forms once paired with the co-mention above.
    RawFeedItem(
        source_name="Test Source",
        source_type="rss",
        source_url="https://example.com/feed",
        source_priority=5,
        source_tags=["test", "cms", "financial"],
        title="CMS rate notice squeezes Medicare Advantage plan margins",
        link="https://example.com/article/cms-rate-notice-margins",
        published=(datetime.utcnow() - timedelta(hours=10)).strftime(
            "%a, %d %b %Y %H:%M:%S +0000"
        ),
        summary=(
            "The annual CMS rate notice sets a lower-than-expected benchmark, squeezing "
            "Medicare Advantage plan margins and forcing insurers to weigh benefit "
            "reductions and premium increases. Analysts expect utilization and medical "
            "cost pressure to compound the effect on earnings."
        ),
    ),
    # Two items dated into the PREVIOUS window (8-12 days back) so the second
    # window drives momentum labels and the causal-chain consistency annotation
    # (policy active last period; financial rising now).
    RawFeedItem(
        source_name="Test Source",
        source_type="rss",
        source_url="https://example.com/feed",
        source_priority=5,
        source_tags=["test", "cms", "policy"],
        title="CMS advance notice previews 2027 Medicare Advantage benchmark and Star Ratings changes",
        link="https://example.com/article/cms-advance-notice-2027",
        published=(datetime.utcnow() - timedelta(days=10)).strftime(
            "%a, %d %b %Y %H:%M:%S +0000"
        ),
        summary=(
            "In an early advance notice, CMS previewed benchmark growth assumptions and "
            "Star Ratings methodology tweaks for Medicare Advantage, setting up a "
            "contentious rate cycle and risk adjustment debate."
        ),
    ),
    RawFeedItem(
        source_name="Test Source",
        source_type="rss",
        source_url="https://example.com/feed",
        source_priority=3,
        source_tags=["test", "membership"],
        title="Medicare Advantage open enrollment tally shows softer membership growth",
        link="https://example.com/article/ma-open-enrollment-tally",
        published=(datetime.utcnow() - timedelta(days=9)).strftime(
            "%a, %d %b %Y %H:%M:%S +0000"
        ),
        summary=(
            "Early open enrollment figures point to softer Medicare Advantage membership "
            "growth than prior years, with several plans reporting slower new member "
            "gains and flat market share."
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

    # Persist all scored items into the browsable story archive (mirrors
    # main._persist_stories) so the web frontend and the Angles page have data
    # to render. Every scored item is written; only above-threshold items — the
    # ones that produced an alert — carry a public draft.
    store = StateStore(root / config.db_path)
    drafts_by_id = {a.scored_item.item.item_id: a.public_draft for a in alerts}
    persisted = 0
    for s in scored:
        primary = classify_item(s, config)
        draft = drafts_by_id.get(s.item.item_id)
        store.upsert_story(
            s,
            primary_category=primary,
            public_draft=asdict(draft) if draft else None,
            states=detect_states(s),
        )
        persisted += 1
    print(f"\nPersisted {persisted} scored items to the story archive")

    # Dedupe check
    new_before = filter_new_items(normalized, store)
    print(f"\nDedupe check: {len(new_before)} would be new items")
    mark_items_seen(normalized, store)
    new_after = filter_new_items(normalized, store)
    print(f"After marking seen: {len(new_after)} new items (should be 0)")
    store.close()


if __name__ == "__main__":
    main()
