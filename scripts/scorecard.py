#!/usr/bin/env python3
"""Print the docs/goal.md scorecard metrics.

Usage:
    python scripts/scorecard.py            # test-suite metrics only (S1, S2, S3, Q1)
    python scripts/scorecard.py --db PATH  # also archive metrics (C2, F1, F3)

The archive DB for --db is typically a fresh download of the published site's
data/state.db, so the numbers reflect production, not a local dev database.
Feature metrics (C1, C3, Q2, U1) are assessed manually — see docs/goal.md.
"""

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

# Add src to path for direct script execution
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import yaml

from ma_signal_monitor.config import load_config
from ma_signal_monitor.models import NormalizedItem
from ma_signal_monitor.scoring import score_item

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE = _PROJECT_ROOT / "tests" / "fixtures" / "golden_set.yaml"
_GOLDEN_TEST = _PROJECT_ROOT / "tests" / "test_golden_set.py"

# Top-10 national MA payers for the C2 coverage metric, grouped by the
# watched-entity aliases that appear in stories.entities.
TOP_PAYERS = {
    "UnitedHealthcare": ["UnitedHealthcare", "UnitedHealth", "UHC"],
    "Humana": ["Humana"],
    "CVS/Aetna": ["CVS Health", "Aetna"],
    "Elevance": ["Elevance", "Anthem"],
    "Centene": ["Centene", "WellCare"],
    "Cigna": ["Cigna"],
    "Kaiser": ["Kaiser"],
    "Molina": ["Molina"],
    "BCBS plans": ["Blue Cross", "Blue Shield", "BCBS"],
    "SCAN": ["SCAN"],
}


def _golden_item(entry: dict) -> NormalizedItem:
    return NormalizedItem(
        item_id="golden",
        source_name="Golden Set",
        source_type="rss",
        source_priority=entry.get("source_priority", 3),
        source_tags=[],
        title=entry["title"],
        link="https://example.com/golden",
        published_date=datetime(2024, 1, 1, 12, 0),
        summary=entry.get("summary", ""),
    )


def golden_metrics(config) -> dict:
    data = yaml.safe_load(_FIXTURE.read_text())
    threshold = config.min_relevance_score
    rel = [
        score_item(_golden_item(e), config).relevance_score for e in data["relevant"]
    ]
    irr = [
        score_item(_golden_item(e), config).relevance_score for e in data["irrelevant"]
    ]
    tp = sum(s >= threshold for s in rel)
    fp = sum(s >= threshold for s in irr)
    fn = len(rel) - tp
    return {
        "size": f"{len(rel) + len(irr)} ({len(rel)} relevant / {len(irr)} irrelevant)",
        "precision": tp / (tp + fp) if (tp + fp) else 1.0,
        "recall": tp / (tp + fn) if (tp + fn) else 1.0,
        "hardest_relevant": min(rel),
        "hardest_irrelevant": max(irr),
        "threshold": threshold,
    }


def ci_floors() -> str:
    text = _GOLDEN_TEST.read_text()
    precision = re.search(r"_PRECISION_FLOOR\s*=\s*([\d.]+)", text)
    recall = re.search(r"_RECALL_FLOOR\s*=\s*([\d.]+)", text)
    if not (precision and recall):
        return "not found"
    return f"{precision.group(1)} / {recall.group(1)}"


def exclusion_counts(config) -> str:
    return f"{len(config.exclusions_hard)} hard / {len(config.exclusions_soft)} soft"


def archive_metrics(db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()
    mentions: Counter = Counter()
    rows = conn.execute(
        "SELECT entities FROM stories WHERE published_date >= ? "
        "AND entities IS NOT NULL AND entities != '[]'",
        (cutoff,),
    ).fetchall()
    for row in rows:
        for entity in json.loads(row["entities"]):
            mentions[entity] += 1
    payer_counts = {
        payer: sum(mentions.get(alias, 0) for alias in aliases)
        for payer, aliases in TOP_PAYERS.items()
    }
    covered = sum(1 for count in payer_counts.values() if count >= 3)

    durations = [
        (
            datetime.fromisoformat(r["run_end"])
            - datetime.fromisoformat(r["run_start"])
        ).total_seconds()
        for r in conn.execute(
            "SELECT run_start, run_end FROM run_metadata "
            "WHERE run_end IS NOT NULL ORDER BY id DESC LIMIT 10"
        )
    ]

    yields = conn.execute(
        """SELECT source_name, count(*) AS n,
                  sum(CASE WHEN relevance_score >= 0.3 THEN 1 ELSE 0 END) AS alerts
           FROM stories GROUP BY source_name HAVING n >= 25"""
    ).fetchall()
    low_yield = [r["source_name"] for r in yields if r["alerts"] / r["n"] < 0.05]

    total = conn.execute("SELECT count(*) FROM stories").fetchone()[0]
    conn.close()
    return {
        "stories": total,
        "payer_counts": payer_counts,
        "covered": covered,
        "durations": durations,
        "high_volume_sources": len(yields),
        "low_yield": low_yield,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Print docs/goal.md scorecard metrics")
    parser.add_argument("--db", type=Path, help="Archive DB (e.g. downloaded state.db)")
    args = parser.parse_args()

    config = load_config(_PROJECT_ROOT)

    print("== Signal quality ==")
    golden = golden_metrics(config)
    print(f"S1  golden-set size:        {golden['size']}")
    print(
        f"S2  precision / recall:     {golden['precision']:.3f} / {golden['recall']:.3f}"
        f"  (threshold {golden['threshold']})"
    )
    print(
        f"    margins:                hardest relevant {golden['hardest_relevant']:.3f},"
        f" hardest irrelevant {golden['hardest_irrelevant']:.3f}"
    )
    print(f"S3  CI floors (P/R):        {ci_floors()}")
    print(f"Q1  exclusions:             {exclusion_counts(config)}")

    if not args.db:
        print("\n(no --db: skipping archive metrics C2/F1/F3)")
        return

    print("\n== Archive (production) ==")
    archive = archive_metrics(args.db)
    print(f"    stories:                {archive['stories']}")
    print(
        f"C2  top-10 payer coverage:  {archive['covered']}/10 with >=3 signals in 30d"
    )
    for payer, count in sorted(archive["payer_counts"].items(), key=lambda kv: -kv[1]):
        marker = " " if count >= 3 else "!"
        print(f"      {marker} {payer}: {count}")
    if archive["durations"]:
        low, high = min(archive["durations"]), max(archive["durations"])
        print(
            f"F1  run wall time (last {len(archive['durations'])}): {low:.0f}-{high:.0f}s"
        )
    print(
        f"F3  low-yield sources:      {len(archive['low_yield'])} of "
        f"{archive['high_volume_sources']} high-volume (<5% alert-grade)"
    )


if __name__ == "__main__":
    main()
