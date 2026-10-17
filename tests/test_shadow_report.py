"""Tests for the offline eligibility-gate shadow report and the holdout window.

The shadow report is how the owner reads what switching the gate on would cost
before switching it on, so each surface it reports is pinned against a story
whose fate is known from the gate's own rules: a Medicaid-only story hidden, a
payer-only criminal story's draft dropped, an MA story kept and briefed, and a
two-alias payer story whose score falls under the threshold once the aliases
collapse.
"""

import json
import sqlite3

import yaml

from evals.relevance import build_holdout, shadow_report

_COLS = (
    "item_id TEXT PRIMARY KEY, title TEXT, summary TEXT, source_name TEXT, "
    "source_priority INTEGER, relevance_score REAL, primary_category TEXT, "
    "entities TEXT, states TEXT, scoring_breakdown TEXT, published_date TEXT, "
    "fetched_at TEXT, duplicate_of TEXT"
)


def _reasons(*contribs):
    return json.dumps(
        [{"factor": f, "detail": "", "contribution": c} for f, c in contribs]
    )


def _db(tmp_path, rows):
    path = tmp_path / "state.db"
    con = sqlite3.connect(path)
    con.execute(f"CREATE TABLE stories ({_COLS})")
    for r in rows:
        con.execute(
            "INSERT INTO stories (item_id, title, summary, source_name, "
            "source_priority, relevance_score, entities, scoring_breakdown, "
            "published_date, fetched_at, duplicate_of) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                r["id"],
                r["title"],
                r.get("summary", ""),
                "Test Source",
                3,
                r["score"],
                json.dumps(r.get("entities", [])),
                r.get("breakdown"),
                r.get("day", "2026-10-02") + "T12:00:00+00:00",
                r.get("day", "2026-10-02") + "T12:00:00+00:00",
                r.get("dup"),
            ),
        )
    con.commit()
    con.close()
    return str(path)


ROWS = [
    {  # MA story: brief tier, survives every surface.
        "id": "a-ma",
        "title": "Humana expands Medicare Advantage D-SNP footprint",
        "score": 0.62,
        "entities": ["Humana"],
        "breakdown": _reasons(("category", 0.27), ("entity_match", 0.2)),
    },
    {  # Medicaid-only: visible today, hidden and undrafted with the gate on.
        "id": "b-medicaid",
        "title": "'Private option' Medicaid expansion under threat from CMS",
        "summary": "Arkansas Medicaid coverage is uncertain.",
        "score": 0.33,
        "breakdown": _reasons(("category", 0.27), ("source_priority", 0.06)),
    },
    {  # Two aliases of one payer: +0.40 today, +0.20 gated, falls under 0.3.
        "id": "c-alias",
        "title": "UnitedHealthcare and UnitedHealth adjust Medicare plan networks",
        "score": 0.46,
        "entities": ["UnitedHealthcare", "UnitedHealth"],
        "breakdown": _reasons(
            ("entity_match", 0.2), ("entity_match", 0.2), ("source_priority", 0.06)
        ),
    },
    {  # A near-duplicate is never counted on any surface.
        "id": "d-dup",
        "title": "Medicaid expansion under threat",
        "score": 0.5,
        "dup": "b-medicaid",
    },
    {  # Outside the window.
        "id": "e-old",
        "title": "Medicaid story from September",
        "score": 0.5,
        "day": "2026-09-20",
    },
]


def _analyse(tmp_path):
    rows = shadow_report.load_rows(_db(tmp_path, ROWS), "2026-10-01", "2026-10-15")
    return rows, shadow_report.analyse(rows)


def test_window_and_duplicates_are_respected(tmp_path):
    rows, _ = _analyse(tmp_path)
    assert {r.item_id for r in rows} == {"a-ma", "b-medicaid", "c-alias"}


def test_feed_hides_only_the_excluded_story(tmp_path):
    _, a = _analyse(tmp_path)
    assert [r.item_id for r in a["hidden"]] == ["b-medicaid"]
    assert a["hidden_by_reason"]  # a reason class is always reported


def test_drafts_drop_noise_and_alias_double_count(tmp_path):
    _, a = _analyse(tmp_path)
    assert {r.item_id for r in a["alerts_now"]} == {"a-ma", "b-medicaid", "c-alias"}
    assert {r.item_id for r in a["alerts_kept"]} == {"a-ma"}
    lost = {r.item_id: r for r in a["alerts_lost"]}
    assert set(lost) == {"b-medicaid", "c-alias"}
    # The alias story is dropped on SCORE, not tier: rebuilt from its breakdown.
    assert lost["c-alias"].gated_score == 0.26
    assert lost["c-alias"].rebuilt


def test_briefing_keeps_only_brief_tier(tmp_path):
    _, a = _analyse(tmp_path)
    (day,) = a["days"]
    assert day["now"] == 3 and day["gated"] == 1
    assert {r.item_id for r in day["leave"]} == {"b-medicaid", "c-alias"}
    assert day["enter"] == []


def test_gated_score_handles_veto_and_missing_breakdown():
    vetoed = _reasons(("category", 0.3), ("exclusion_veto", -0.3))
    assert shadow_report._gated_score(0.0, vetoed, 0.0) == (0.0, True)
    assert shadow_report._gated_score(0.5, None, -0.2) == (0.3, False)


def test_render_is_complete_markdown(tmp_path):
    _, a = _analyse(tmp_path)
    md = shadow_report.render(a, "2026-10-01", "2026-10-15")
    assert md.startswith("# Eligibility gate shadow report, 2026-10-01 to 2026-10-15")
    for heading in ("## Feed", "## Public drafts", "## Daily Briefing"):
        assert heading in md
    assert "Visible today: 3. Hidden with the gate on: 1 (33%)" in md


def test_holdout_window_and_exclusion(tmp_path):
    db = _db(tmp_path, ROWS)
    prior = tmp_path / "prior.yaml"
    prior.write_text(yaml.safe_dump({"items": [{"item_id": "a-ma"}]}))

    data = build_holdout.build(
        db,
        since="2026-10-01",
        until="2026-10-15",
        exclude_ids=build_holdout.load_excluded_ids([str(prior)]),
    )
    ids = {it["item_id"] for it in data["items"]}
    assert ids == {"b-medicaid", "c-alias"}  # window, dedup and exclusion
    assert data["_meta"]["heldout_until"] == "2026-10-15"
    assert data["_meta"]["excluded_prior_items"] == 1


def test_holdout_defaults_add_no_meta_keys(tmp_path):
    # The August set must regenerate byte-identically: no new keys by default.
    data = build_holdout.build(_db(tmp_path, ROWS))
    assert "heldout_until" not in data["_meta"]
    assert "excluded_prior_items" not in data["_meta"]
    assert data["_meta"]["heldout_since"] == build_holdout.HELDOUT_SINCE
