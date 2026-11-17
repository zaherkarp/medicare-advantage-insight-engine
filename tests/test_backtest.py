"""Tests for the selection-policy back-test (evals/relevance/backtest.py).

Each policy is pinned on stories whose fate follows from its rule: an MA story
under the threshold (only B and C surface it), a Medicaid story over it (A and
C keep it, the gate drops it), and an ordinary story under it (nobody does).
"""

import json
import sqlite3

import yaml

from evals.relevance import backtest

_COLS = (
    "item_id TEXT PRIMARY KEY, title TEXT, summary TEXT, source_name TEXT, "
    "relevance_score REAL, entities TEXT, published_date TEXT, fetched_at TEXT, "
    "duplicate_of TEXT"
)

ROWS = [
    (
        "ma-low",
        "Providence Health Plan to close after Medicare Advantage deal fails",
        0.21,
    ),
    (
        "medicaid-high",
        "'Private option' Medicaid expansion under threat from CMS",
        0.33,
    ),
    ("noise-low", "Ohio school districts post new report cards", 0.05),
]


def _db(tmp_path, rows=ROWS, day="2026-10-20"):
    path = tmp_path / "state.db"
    con = sqlite3.connect(path)
    con.execute(f"CREATE TABLE stories ({_COLS})")
    for item_id, title, score in rows:
        con.execute(
            "INSERT INTO stories VALUES (?,?,?,?,?,?,?,?,NULL)",
            (item_id, title, "", "Src", score, json.dumps([]), day, day),
        )
    con.commit()
    con.close()
    return str(path)


def test_policy_rules():
    a, b, c, d = (backtest.POLICIES[k][1] for k in "ABCD")
    assert a(0.3, "exclude") and not a(0.29, "brief")
    assert b(0.0, "brief") and not b(0.9, "alert")
    assert c(0.1, "brief") and c(0.5, "exclude") and not c(0.2, "display")
    assert d(0.5, "display") and not d(0.5, "exclude")


def test_volume_counts_and_c_additions(tmp_path):
    v = backtest.volume(_db(tmp_path), "2026-10-01", None)
    assert v["totals"]["A"] == 1  # medicaid-high
    assert v["totals"]["C"] == 2  # plus the MA story the gate rescues
    assert v["totals"]["D"] == 0  # the gate calls the Medicaid story noise
    assert [r["item_id"] for r in v["added"]] == ["ma-low"]


def test_volume_respects_window(tmp_path):
    v = backtest.volume(_db(tmp_path), "2026-11-01", None)
    assert sum(v["totals"].values()) == 0


def _holdout(tmp_path, labels, excluded=None):
    items = []
    for (item_id, title, score), label in zip(ROWS, labels):
        items.append(
            {
                "item_id": item_id,
                "title": title,
                "summary": "",
                "score_at_capture": score,
                "label": label,
                "proposed_label": "irrelevant",
            }
        )
    meta = {"n": len(items), "heldout_since": "2026-10-01"}
    if excluded:
        meta["excluded_from_scoring"] = {"items": {i: "dev" for i in excluded}}
    p = tmp_path / "h.yaml"
    p.write_text(yaml.safe_dump({"_meta": meta, "items": items}))
    return str(p)


def test_accuracy_per_policy(tmp_path):
    h = _holdout(tmp_path, ["relevant_brief", "relevant_context", "irrelevant"])
    a = backtest.accuracy(h)
    assert (a["brief"], a["wanted"]) == (1, 2)
    assert a["policies"]["A"]["brief_caught"] == 0
    assert a["policies"]["C"]["brief_caught"] == 1
    assert a["policies"]["C"]["wanted_caught"] == 2
    assert a["policies"]["B"]["wanted_caught"] == 1


def test_accuracy_honours_exclusions(tmp_path):
    h = _holdout(
        tmp_path, ["relevant_brief", "relevant_context", "irrelevant"], ["ma-low"]
    )
    a = backtest.accuracy(h)
    assert a["n"] == 2 and a["brief"] == 0


def test_disagreements_skip_items_already_labeled(tmp_path):
    v = backtest.volume(_db(tmp_path), "2026-10-01", None)
    prior = tmp_path / "prior.yaml"
    prior.write_text(yaml.safe_dump({"items": [{"item_id": "ma-low"}]}))
    assert backtest.disagreements(v["added"], [], "2026-10-01")["_meta"]["n"] == 1
    assert (
        backtest.disagreements(v["added"], [str(prior)], "2026-10-01")["_meta"]["n"]
        == 0
    )


def test_render_has_every_section(tmp_path):
    h = _holdout(tmp_path, ["relevant_brief", "relevant_context", "irrelevant"])
    md = backtest.render(
        backtest.volume(_db(tmp_path), "2026-10-01", None),
        {h: backtest.accuracy(h)},
        "2026-10-01",
        None,
    )
    assert "## Volume" in md and "## Accuracy" in md and "C adds 1 stories" in md
