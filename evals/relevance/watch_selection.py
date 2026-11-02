"""Deterministic NEWS-WATCH-03 current-versus-candidate selection replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import yaml

from ma_signal_monitor.angles import build_angles
from ma_signal_monitor.config import load_config
from ma_signal_monitor.digest import build_digest
from ma_signal_monitor.drafting import draft_alerts
from ma_signal_monitor.main import _persist_stories
from ma_signal_monitor.models import NormalizedItem
from ma_signal_monitor.scoring import score_items
from evals.relevance.watch_candidate_scoring import score_items as candidate_score_items
from ma_signal_monitor.storage import StateStore
from ma_signal_monitor.web.routes import _facet_view, _story_view

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


def _run_surface(cases: list[dict], *, mode: str) -> dict[str, dict]:
    with patch.dict(os.environ, {"SEC_CONTACT_EMAIL": "replay@example.invalid"}):
        config = load_config(ROOT)
    config.ma_eligibility_gate = mode == "gate_experiment"
    if mode != "current":
        config.scoring.research_context_boost = 0.10  # offline candidate only
    config.archive_min_score = 0.1
    config.min_relevance_score = 0.3
    config.digest_min_score = 0.3
    # Cover the bounded fixture window, including KFF's October 13 publication
    # date.  The previous 48-hour clock silently made that known miss look
    # absent from Briefing even after it cleared the score threshold.
    config.digest_lookback_hours = 24 * 14

    # Baseline executes current production scoring unchanged. The retained
    # scorer is an explicit offline candidate, never imported by production.
    scorer = score_items if mode == "current" else candidate_score_items
    with tempfile.TemporaryDirectory(prefix="news-watch-selection-") as tmp:
        store = StateStore(
            Path(tmp) / "state.db", eligibility_gate=config.ma_eligibility_gate
        )
        scored = scorer([_item(case) for case in cases], config)
        by_id = {row.item.item_id: row for row in scored}
        alerts = draft_alerts(scored, config)
        _persist_stories(scored, alerts, config, store)

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
            _facet_view(row)
            for row in store.get_recent_story_facets(
                since=datetime.fromisoformat("2026-10-07T00:00:00"),
                min_score=config.archive_min_score,
                until=datetime.fromisoformat("2026-10-21T00:00:00"),
            )
        ]
        angle_ids = {
            story["item_id"]
            for card in build_angles(facets, [], config)["angles"]
            for story in card["stories"]
        }
        alert_ids = {alert.scored_item.item.item_id for alert in alerts}
        result = {
            case["id"]: {
                "fetched": "unknown",
                "archived": store.get_story(case["id"]) is not None,
                "displayed": case["id"] in displayed,
                "briefing": case["id"] in briefing_ids,
                "angles": case["id"] in angle_ids,
                "drafted": bool(
                    _story_view(store.get_story(case["id"]))["public_draft"]
                ),
                "saved_draft": _story_view(store.get_story(case["id"]))["public_draft"],
                "draft_selected": case["id"] in alert_ids,
                "score": by_id[case["id"]].relevance_score,
                "eligibility_tier": by_id[case["id"]].eligibility_tier,
                "matched_entities": by_id[case["id"]].matched_entities,
                "duplicate_of": store.get_story(case["id"])["duplicate_of"],
                "facet_types": {
                    key: type(
                        next(row[key] for row in facets if row["item_id"] == case["id"])
                    ).__name__
                    for key in ("categories", "entities", "states")
                }
                if case["id"] in displayed
                and store.get_story(case["id"])["duplicate_of"] is None
                else None,
            }
            for case in cases
        }
        store.close()
        return result


def run_replay(cases_path: Path = DEFAULT_CASES) -> dict:
    fixture = yaml.safe_load(cases_path.read_text(encoding="utf-8"))
    cases = fixture["cases"]
    current = _run_surface(cases, mode="current")
    candidate = _run_surface(cases, mode="candidate_gate_off")
    gate_experiment = _run_surface(cases, mode="gate_experiment")
    rows = []
    for case in cases:
        expected = {
            ("candidate_gate_off" if key == "shipped_after_commit" else key): value
            for key, value in case["expected"].items()
        }
        observed = {
            "current": current[case["id"]],
            "candidate_gate_off": candidate[case["id"]],
            "gate_experiment": gate_experiment[case["id"]],
        }
        passed = all(
            all(observed[surface][field] == value for field, value in fields.items())
            for surface, fields in expected.items()
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
                **observed,
                "passed": passed,
            }
        )
    return {
        "source_identity": {
            "retained_candidate_commit": "854b43c0253f8ab0bc64f9370c7c2c7f16fef90d",
            "current_scorer_sha256": hashlib.sha256(
                (ROOT / "src/ma_signal_monitor/scoring.py").read_bytes()
            ).hexdigest(),
            "taxonomy_sha256": hashlib.sha256(
                (ROOT / "config/taxonomy.yaml").read_bytes()
            ).hexdigest(),
            "fixture_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        },
        "fixture_version": fixture["version"],
        "retrieved_at": str(fixture["retrieved_at"]),
        "index_coverage": fixture["index_coverage"],
        "config_identity": {
            "current": "current production scorer; gate off; unchanged taxonomy",
            "candidate_gate_off": "OFFLINE ONLY; gate off; retained contextual matcher and research_context_boost=0.10",
            "gate_experiment": "hypothetical gate on; contextual alias matcher; research_context_boost=0.10",
            "production_flags_changed": False,
            "replay_overrides": "archive_min_score=0.1; min_relevance_score=digest_min_score=0.3; 14-day fixed October fixture window",
        },
        "production_paths": {
            "persistence_classification_and_duplicates": "main._persist_stories",
            "saved_story_readback": "web.routes._story_view",
            "decoded_angles_facets": "web.routes._facet_view",
        },
        "coverage_limits": [
            "Source-shaped fixtures do not prove acquisition, recall, or precision.",
            "The 8d11/5ff2/942e controls preserve item identities and recorded failure shapes, but their minimized text is not a byte-for-byte copy of the unavailable private comparison bundle.",
            "Fetched remains unknown because no live source adapter is exercised.",
            "No production per-claim support gate exists; unsupported numerical claims remain a product gap.",
            "Fixture expectations are frozen development recommendations, not owner verdicts, and do not authorize production configuration changes.",
            "Healthfirst retention is topical; matched_entities remains empty, so payer identity support is not claimed.",
            "Both candidate modes remain offline experiments. The current scorer now has a separately tested SCAN identity repair; research boosting and the broader gate remain unpromoted.",
            "PR75 web-only production persistence is used; no retired outbound alert or delivery path is restored.",
            "PR77 owns the broader eligibility-gate evaluation; these 13 tuning fixtures do not supersede its October holdout, owner-label process, recall evidence, or gate decision.",
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
