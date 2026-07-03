"""Regression test: the live taxonomy must keep classifying a hand-labelled set.

Scores ``tests/fixtures/golden_set.yaml`` with the real ``config/taxonomy.yaml``
and asserts precision and recall stay above conservative floors. A taxonomy or
scoring change that would have broken past judgments fails here instead of
silently shifting the alert stream.
"""

from datetime import datetime
from pathlib import Path

import yaml

from ma_signal_monitor.config import load_config
from ma_signal_monitor.models import NormalizedItem
from ma_signal_monitor.scoring import score_item

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = Path(__file__).parent / "fixtures" / "golden_set.yaml"

# Floors guard against regressions; they tolerate the documented KNOWN-GAP
# entries in the fixture (currently 3 misses + 3 false positives out of 83,
# i.e. ~0.914/0.914) but fail if a change misclassifies even one more case.
# Per docs/goal.md these floors may only ever be raised, never lowered.
_PRECISION_FLOOR = 0.9
_RECALL_FLOOR = 0.9


def _item(entry: dict) -> NormalizedItem:
    # Entries default to priority 3 (a trusted, ungated MA source). Entries may
    # set source_priority: 2 to exercise the MA-context gate that broad,
    # low-priority feeds are subject to (scoring.ma_context_min_priority).
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


def test_golden_set_precision_recall():
    config = load_config(_PROJECT_ROOT)
    data = yaml.safe_load(_FIXTURE.read_text())
    threshold = config.min_relevance_score

    tp = fp = fn = tn = 0
    misses = []
    for entry in data["relevant"]:
        score = score_item(_item(entry), config).relevance_score
        if score >= threshold:
            tp += 1
        else:
            fn += 1
            misses.append(("expected relevant", entry["title"], score))
    for entry in data["irrelevant"]:
        score = score_item(_item(entry), config).relevance_score
        if score >= threshold:
            fp += 1
            misses.append(("expected irrelevant", entry["title"], score))
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    detail = "\n".join(f"  {why}: {title!r} scored {s:.3f}" for why, title, s in misses)
    assert precision >= _PRECISION_FLOOR, (
        f"precision {precision:.2f} < {_PRECISION_FLOOR} (threshold {threshold})\n{detail}"
    )
    assert recall >= _RECALL_FLOOR, (
        f"recall {recall:.2f} < {_RECALL_FLOOR} (threshold {threshold})\n{detail}"
    )
