"""Deterministic NEWS-TRENDS-01 replay through production selectors.

The small fixture is a regression pack, not a precision/recall sample. It uses
the real archive, Briefing, Angles-facet, alert and alert-dedup code paths. The
eligibility gate is enabled only inside the isolated replay; shipped config
remains default-off. ``draft_candidate`` is deliberately stricter than alert
selection: a selected alert with conflicting/unsupported figures is recorded
but never promoted to a draft candidate by this evaluation harness.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime
from pathlib import Path

import yaml

from ma_signal_monitor.config import AppConfig
from ma_signal_monitor.dedupe import suppress_duplicate_alerts
from ma_signal_monitor.digest import build_digest
from ma_signal_monitor.drafting import draft_alerts
from ma_signal_monitor.models import NormalizedItem, ScoredItem
from ma_signal_monitor.storage import StateStore

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = Path(__file__).with_name("product_replay_cases.yaml")


def _scored(case: dict, now: datetime) -> ScoredItem:
    item = NormalizedItem(
        item_id=case["id"],
        source_name="NEWS-TRENDS-01 replay",
        source_type="fixture",
        source_priority=5,
        source_tags=["regression"],
        title=case["title"],
        link=f"https://example.invalid/{case['id']}",
        published_date=now,
        summary=case["summary"],
    )
    return ScoredItem(
        item=item,
        relevance_score=float(case["score"]),
        matched_categories=list(case.get("categories", [])),
        eligibility_tier=case.get("tier"),
        eligibility_reason=f"replay:{case.get('tier', 'unclassified')}",
    )


def run_replay(cases_path: Path = DEFAULT_CASES) -> dict:
    fixture = yaml.safe_load(cases_path.read_text(encoding="utf-8"))
    now = datetime.fromisoformat(str(fixture["as_of"]))
    cases = fixture["cases"]
    config = AppConfig(
        ma_eligibility_gate=True,
        archive_min_score=0.1,
        min_relevance_score=0.3,
        digest_min_score=0.3,
        digest_lookback_hours=24,
    )

    with tempfile.TemporaryDirectory(prefix="news-trends-replay-") as tmp:
        store = StateStore(Path(tmp) / "state.db", eligibility_gate=True)
        scored = [_scored(case, now) for case in cases]
        for case, story in zip(cases, scored, strict=True):
            store.upsert_story(
                story,
                primary_category=story.matched_categories[0],
                duplicate_of=case.get("duplicate_of"),
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
        angle_ids = {
            row["item_id"]
            for row in store.get_recent_story_facets(
                since=since, min_score=config.archive_min_score
            )
        }
        alerts, suppressed = suppress_duplicate_alerts(
            draft_alerts(scored, config), store, config
        )
        alert_ids = {alert.scored_item.item.item_id for alert in alerts}

        results = []
        for case in cases:
            selected = {
                "feed": case["id"] in feed_ids,
                "briefing": case["id"] in briefing_ids,
                "angles": case["id"] in angle_ids,
                "alert": case["id"] in alert_ids,
            }
            selected["draft_candidate"] = selected["alert"] and case.get(
                "claim_support", "supported"
            ) == "supported"
            expected = case["expected"]
            results.append(
                {
                    "id": case["id"],
                    "expected": expected,
                    "observed": selected,
                    "passed": selected == expected,
                    "claim_support": case.get("claim_support", "supported"),
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
        },
        "coverage_limits": [
            "Synthetic, source-shaped cases do not prove production recall or precision.",
            "No private archive or owner labels are used.",
            "Angles records actual facet eligibility, not whether two-story card density is met.",
        ],
        "alert_duplicates_suppressed": suppressed,
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
