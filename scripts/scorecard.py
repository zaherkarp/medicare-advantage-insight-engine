#!/usr/bin/env python3
"""Print the docs/goal.md scorecard metrics.

Usage:
    python scripts/scorecard.py            # test-suite metrics only (S1, S2, S3, Q1)
    python scripts/scorecard.py --db PATH  # also archive metrics (C2, F1, F3, N1)

The archive DB for --db is typically a fresh copy of production's
data/state.db, so the numbers reflect production, not a local dev database.
As of the actions/cache persistence change in deploy-pages.yml, that DB is no
longer publicly downloadable; see the note in scripts/calibrate_threads.py's
docstring for how to pull a copy via a temporary workflow artifact upload.
Feature metrics (C1, C3, Q2, U1) are assessed manually — see docs/goal.md.

N1 (display-floor composition) is not yet a docs/goal.md row — it was added to
catch a class of noise F3 and source_review.flag_low_yield_sources can't see:
both measure yield against the 0.3 *alert* threshold, so a source that
reliably clears the 0.1 *display* floor (archive_min_score) but never alerts
never shows up as low-yield. N1 measures composition at the display floor
instead, using the same MA-context predicate the scorer itself gates on.
"""

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# Add src to path for direct script execution
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import yaml

from ma_signal_monitor.config import AppConfig, load_config
from ma_signal_monitor.models import NormalizedItem
from ma_signal_monitor.scoring import _has_ma_context, _keyword_in_text, score_item

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

# N1 display-floor composition: same rolling window as the C2 mentions query
# and the production measurement that motivated ma_context_min_priority (see
# config/taxonomy.yaml). A source needs at least this many display-floor
# stories in the window before its composition is reported individually —
# smaller than F3's archive-wide 25 because this is one 30-day window, not
# the whole archive's history.
_DISPLAY_FLOOR_WINDOW_DAYS = 30
_DISPLAY_FLOOR_SOURCE_MIN_SAMPLE = 10
_DISPLAY_FLOOR_WORST_N = 5

# Ordered from most to least specific. A story lands in the first tier whose
# test it satisfies:
#   ma_vocab         -- strong MA-plan vocabulary (config.ma_boost_terms),
#                        direct evidence of an MA-market signal on its own.
#   payer            -- names a watched_entities payer but no ma_boost_terms.
#   medicare_or_cms  -- on-domain per _has_ma_context (a config.ma_context_terms
#                        anchor: bare "Medicare"/"CMS"/"risk adjustment"/...)
#                        but neither of the above — context established, but
#                        not by itself MA-market or payer-specific evidence.
#   none             -- fails _has_ma_context entirely: the off-domain noise
#                        profile ma_context_min_priority exists to gate.
_CONTEXT_TIERS = ("ma_vocab", "payer", "medicare_or_cms", "none")


def _context_tier(text: str, config: AppConfig) -> str:
    """Classify already-lowercased ``text`` into one of :data:`_CONTEXT_TIERS`.

    Reuses ``scoring._has_ma_context`` for the on/off-domain split (the exact
    predicate ``score_item``'s MA-context gate applies) so this report and the
    gate can never drift apart; only the on-domain subdivision into
    "ma_vocab"/"payer"/"medicare_or_cms" is done here, via the same
    whole-token ``_keyword_in_text`` matcher scoring.py uses everywhere else.
    """
    if not _has_ma_context(text, config):
        return "none"
    if any(_keyword_in_text(term, text) for term in config.ma_boost_terms):
        return "ma_vocab"
    if any(_keyword_in_text(entity, text) for entity in config.watched_entities):
        return "payer"
    return "medicare_or_cms"


def display_floor_composition(db_path: Path, config: AppConfig) -> dict:
    """Bucket recent display-floor stories by MA-context tier (N1).

    Mirrors the production measurement behind ``ma_context_min_priority``:
    non-duplicate stories at/above ``archive_min_score`` in the last
    :data:`_DISPLAY_FLOOR_WINDOW_DAYS` days, using each row's *stored*
    ``relevance_score`` (what actually shipped to the archive), not a fresh
    re-score — this reports what is on the site today, not a hypothetical.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cutoff = (
        datetime.utcnow() - timedelta(days=_DISPLAY_FLOOR_WINDOW_DAYS)
    ).isoformat()
    rows = conn.execute(
        """SELECT source_name, title, summary FROM stories
           WHERE duplicate_of IS NULL
             AND COALESCE(relevance_score, 0) >= ?
             AND COALESCE(published_date, fetched_at) >= ?""",
        (config.archive_min_score, cutoff),
    ).fetchall()
    conn.close()

    overall: Counter = Counter()
    by_source: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        text = f"{row['title'] or ''} {row['summary'] or ''}".lower()
        tier = _context_tier(text, config)
        overall[tier] += 1
        by_source[row["source_name"]][tier] += 1

    total = sum(overall.values())
    worst = sorted(
        (
            (name, counts)
            for name, counts in by_source.items()
            if sum(counts.values()) >= _DISPLAY_FLOOR_SOURCE_MIN_SAMPLE
        ),
        key=lambda kv: (kv[1]["none"] / sum(kv[1].values()), kv[1]["none"]),
        reverse=True,
    )[:_DISPLAY_FLOOR_WORST_N]

    return {
        "days": _DISPLAY_FLOOR_WINDOW_DAYS,
        "total": total,
        "overall": overall,
        "worst_sources": worst,
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
        print("\n(no --db: skipping archive metrics C2/F1/F3/N1)")
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

    dfc = display_floor_composition(args.db, config)
    total = dfc["total"]
    overall = dfc["overall"]
    print(
        f"N1  display-floor composition ({dfc['days']}d, {total} stories "
        f">= {config.archive_min_score} floor, non-duplicate):"
    )
    for tier, label in (
        ("ma_vocab", "MA-specific vocabulary"),
        ("payer", "names a watched payer"),
        ("medicare_or_cms", "Medicare/CMS mention only"),
        ("none", "none of these (off-domain noise)"),
    ):
        n = overall.get(tier, 0)
        share = n / total if total else 0.0
        marker = "!" if tier == "none" and share > 0.05 else " "
        print(f"    {marker} {label + ':':<36} {n:>5}  ({share:.1%})")
    if dfc["worst_sources"]:
        print(
            f"    worst sources by off-domain share (>= "
            f"{_DISPLAY_FLOOR_SOURCE_MIN_SAMPLE} in-window stories):"
        )
        for name, counts in dfc["worst_sources"]:
            n = sum(counts.values())
            none_n = counts.get("none", 0)
            print(f"      {none_n / n:.1%} off-domain  ({none_n}/{n})  {name}")


if __name__ == "__main__":
    main()
