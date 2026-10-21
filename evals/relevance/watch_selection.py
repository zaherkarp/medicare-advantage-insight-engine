"""Deterministic NEWS-WATCH-03 current-versus-candidate selection replay."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import yaml

from ma_signal_monitor.angles import build_angles
from ma_signal_monitor.config import load_config
from ma_signal_monitor.digest import build_digest
from ma_signal_monitor.drafting import draft_alerts
from ma_signal_monitor.models import NormalizedItem
from ma_signal_monitor.scoring import _keyword_in_text, score_items
from ma_signal_monitor.storage import StateStore

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = Path(__file__).with_name("watch_selection_cases.yaml")


def _item(case: dict) -> NormalizedItem:
    return NormalizedItem(
        item_id=case["id"],
        source_name=case["source_name"],
        source_type="fixture",
        source_priority=case["source_priority"],
        source_tags=case["source_tags"],
        title=case["title"],
        link=case["source_url"],
        published_date=datetime.fromisoformat(str(case["publication_date"])),
        fetched_at=datetime.fromisoformat(str(case["first_seen"])),
        summary=case["summary"],
    )


def _run_surface(cases: list[dict], *, candidate: bool) -> dict[str, dict]:
    with patch.dict(os.environ, {"SEC_CONTACT_EMAIL": "replay@example.invalid"}):
        config = load_config(ROOT)
    config.ma_eligibility_gate = candidate
    config.scoring.research_evidence_boost = 0.10 if candidate else 0.0
    config.archive_min_score = 0.1
    config.min_relevance_score = 0.3
    config.digest_min_score = 0.3
    config.digest_lookback_hours = 48

    # Reproduce the prior case-insensitive bare-alias behavior for the current
    # side without retaining that behavior in production code.
    matcher = nullcontext()
    if not candidate:
        matcher = patch(
            "ma_signal_monitor.scoring._entity_in_text",
            side_effect=lambda entity, text: _keyword_in_text(entity, text),
        )
    with matcher, tempfile.TemporaryDirectory(prefix="news-watch-selection-") as tmp:
        store = StateStore(Path(tmp) / "state.db", eligibility_gate=candidate)
        scored = score_items([_item(case) for case in cases], config)
        by_id = {row.item.item_id: row for row in scored}
        for row in scored:
            store.upsert_story(
                row,
                primary_category=(row.matched_categories or ["uncategorized"])[0],
                threshold_at_score=config.min_relevance_score,
            )

        displayed = {
            row["item_id"]
            for row in store.get_stories(min_score=config.archive_min_score, limit=100)
        }
        briefing = build_digest(
            store, config, now=datetime.fromisoformat("2026-10-20T23:00:00")
        )
        briefing_ids = {
            story.item_id for _label, stories in briefing.sections for story in stories
        }
        facets = [
            dict(row)
            for row in store.get_recent_story_facets(
                since=datetime.fromisoformat("2026-10-20T00:00:00"),
                min_score=config.archive_min_score,
            )
        ]
        angle_ids = {
            story["item_id"]
            for card in build_angles(facets, [], config)["angles"]
            for story in card["stories"]
        }
        drafted_ids = {
            alert.scored_item.item.item_id for alert in draft_alerts(scored, config)
        }
        result = {
            case["id"]: {
                "fetched": "unknown",
                "archived": True,
                "displayed": case["id"] in displayed,
                "briefing": case["id"] in briefing_ids,
                "angles": case["id"] in angle_ids,
                "drafted": case["id"] in drafted_ids,
                "legacy_alert": case["id"] in drafted_ids,
                "score": by_id[case["id"]].relevance_score,
                "eligibility_tier": by_id[case["id"]].eligibility_tier,
                "matched_entities": by_id[case["id"]].matched_entities,
            }
            for case in cases
        }
        store.close()
        return result


def run_replay(cases_path: Path = DEFAULT_CASES) -> dict:
    fixture = yaml.safe_load(cases_path.read_text(encoding="utf-8"))
    cases = fixture["cases"]
    current = _run_surface(cases, candidate=False)
    candidate = _run_surface(cases, candidate=True)
    rows = []
    for case in cases:
        expected = case["expected"]
        passed = (
            current[case["id"]]["drafted"] == expected["current_drafted"]
            and candidate[case["id"]]["drafted"] == expected["candidate_drafted"]
        )
        rows.append(
            {
                "id": case["id"],
                "source_url": case["source_url"],
                "dates": {
                    key: str(case[key]) if case.get(key) is not None else None
                    for key in (
                        "publication_date",
                        "event_date",
                        "first_seen",
                        "data_period",
                        "effective_date",
                    )
                },
                "recommendation": case["recommendation"],
                "label_provenance": fixture["label_provenance"],
                "expected": expected,
                "current": current[case["id"]],
                "candidate": candidate[case["id"]],
                "passed": passed,
            }
        )
    return {
        "fixture_version": fixture["version"],
        "retrieved_at": str(fixture["retrieved_at"]),
        "index_coverage": fixture["index_coverage"],
        "config_identity": {
            "current": "shipped flags; research_evidence_boost=0; legacy alias matcher",
            "candidate": "eligibility replay on; contextual alias matcher; research_evidence_boost=0.10",
            "production_flags_changed": False,
        },
        "coverage_limits": [
            "Source-shaped fixtures do not prove acquisition, recall, or precision.",
            "Fetched remains unknown because no live source adapter is exercised.",
            "No production per-claim support gate exists; unsupported numerical claims remain a product gap.",
            "Tuning fixtures must not be reused as the fresh unseen evaluation window.",
        ],
        "results": rows,
        "passed": all(row["passed"] for row in rows),
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
