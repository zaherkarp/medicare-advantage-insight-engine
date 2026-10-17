"""Deterministic NEWS-TRENDS-01 replay through production product paths.

The fixture is a compact regression pack, not a precision/recall sample. It
starts with source-shaped items and delegates scoring, MA eligibility, archive
duplicate assignment, Briefing selection, Angles card construction, alert
selection, and alert deduplication to production code. The eligibility gate is
enabled only inside this isolated replay; shipped configuration remains off.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import yaml

from ma_signal_monitor.angles import build_angles
from ma_signal_monitor.config import load_config
from ma_signal_monitor.dedupe import assign_story_duplicates, suppress_duplicate_alerts
from ma_signal_monitor.digest import build_digest
from ma_signal_monitor.drafting import draft_alerts
from ma_signal_monitor.models import NormalizedItem
from ma_signal_monitor.scoring import score_items
from ma_signal_monitor.storage import StateStore

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = Path(__file__).with_name("product_replay_cases.yaml")


def _source_item(case: dict) -> NormalizedItem:
    return NormalizedItem(
        item_id=case["id"],
        source_name="NEWS-TRENDS-01 replay",
        source_type="fixture",
        source_priority=5,
        source_tags=["regression", f"rating-year-{case['rating_year']}"],
        title=case["title"],
        link=f"https://example.invalid/{case['id']}",
        published_date=datetime.fromisoformat(str(case["event_date"])),
        fetched_at=datetime.fromisoformat(str(case["first_seen"])),
        summary=case["summary"],
    )


def run_replay(cases_path: Path = DEFAULT_CASES) -> dict:
    fixture = yaml.safe_load(cases_path.read_text(encoding="utf-8"))
    now = datetime.fromisoformat(str(fixture["as_of"]))
    cases = fixture["cases"]

    # Load the shipped taxonomy, weights, duplicate thresholds, and causal
    # model. Only the experimental eligibility gate is changed for this replay.
    # Loading validates enabled SEC sources even though the replay never fetches
    # them. Supply a non-routable placeholder only for config validation.
    with patch.dict(os.environ, {"SEC_CONTACT_EMAIL": "replay@example.invalid"}):
        config = load_config(ROOT)
    config.ma_eligibility_gate = True
    config.archive_min_score = 0.1
    config.min_relevance_score = 0.3
    config.digest_min_score = 0.3
    config.digest_lookback_hours = 24

    with tempfile.TemporaryDirectory(prefix="news-trends-replay-") as tmp:
        store = StateStore(Path(tmp) / "state.db", eligibility_gate=True)
        scored = score_items([_source_item(case) for case in cases], config)
        by_id = {story.item.item_id: story for story in scored}
        duplicate_map = assign_story_duplicates(scored, store, config)
        for story in scored:
            store.upsert_story(
                story,
                primary_category=(story.matched_categories or ["uncategorized"])[0],
                duplicate_of=duplicate_map[story.item.item_id],
                threshold_at_score=config.min_relevance_score,
            )

        feed_ids = {
            row["item_id"]
            for row in store.get_stories(min_score=config.archive_min_score, limit=100)
        }
        briefing = build_digest(store, config, now=now)
        briefing_ids = {
            story.item_id for _label, stories in briefing.sections for story in stories
        }

        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
        facet_rows = [
            dict(row)
            for row in store.get_recent_story_facets(
                since=since, min_score=config.archive_min_score
            )
        ]
        angles_view = build_angles(facet_rows, [], config)
        angle_ids = {
            story["item_id"]
            for card in angles_view["angles"]
            for story in card["stories"]
        }

        alerts, alert_duplicates_suppressed = suppress_duplicate_alerts(
            draft_alerts(scored, config), store, config
        )
        alert_ids = {alert.scored_item.item.item_id for alert in alerts}

        results = []
        for case in cases:
            story = by_id[case["id"]]
            observed = {
                "feed": case["id"] in feed_ids,
                "briefing": case["id"] in briefing_ids,
                "angles": case["id"] in angle_ids,
                "alert": case["id"] in alert_ids,
            }
            results.append(
                {
                    "id": case["id"],
                    "rating_year": case["rating_year"],
                    "event_date": str(case["event_date"]),
                    "first_seen": str(case["first_seen"]),
                    "score": story.relevance_score,
                    "categories": story.matched_categories,
                    "eligibility_tier": story.eligibility_tier,
                    "duplicate_of": duplicate_map[case["id"]],
                    "expected": case["expected"],
                    "observed": observed,
                    "passed": observed == case["expected"],
                }
            )
        store.close()

    return {
        "fixture_version": fixture["version"],
        "selector_config": {
            "eligibility_gate": True,
            "shipped_gate_default": False,
            "archive_min_score": config.archive_min_score,
            "briefing_min_score": config.digest_min_score,
            "alert_min_score": config.min_relevance_score,
            "story_duplicate_similarity": config.dedup_similarity_threshold,
        },
        "production_paths": {
            "scoring_and_eligibility": "score_items",
            "archive_duplicate_assignment": "assign_story_duplicates",
            "angles_cards": "build_angles",
            "alert_selection": "draft_alerts",
            "alert_duplicate_suppression": "suppress_duplicate_alerts",
        },
        "drafting_guard": {
            "available": False,
            "status": "product_gap",
            "detail": (
                "No production claim-support guard exists between alert selection "
                "and public-draft construction; this replay does not simulate one."
            ),
        },
        "coverage_limits": [
            "Synthetic, source-shaped cases do not prove production recall or precision.",
            "No private archive or owner labels are used.",
            "The product has no production claim-support drafting guard to exercise.",
        ],
        "angles_card_count": len(angles_view["angles"]),
        "angles_card_types": sorted({card["type"] for card in angles_view["angles"]}),
        "archive_duplicates_assigned": sum(v is not None for v in duplicate_map.values()),
        "alert_duplicates_suppressed": alert_duplicates_suppressed,
        "results": results,
        "passed": all(row["passed"] for row in results),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_replay(args.cases)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
