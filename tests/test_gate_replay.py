import sqlite3
from dataclasses import replace
from datetime import timedelta

import pytest

from evals.relevance.gate_replay import (
    Evidence,
    Story,
    acceptance,
    archive_clocks,
    evaluate,
    load_archive,
    timestamp,
)
from ma_signal_monitor.config import AppConfig

START = timestamp("2026-10-01T00:00:00+00:00")
CLOCK = START + timedelta(hours=12)
END = START + timedelta(days=1)


def story(item_id, score, tier, *, alias=None, fetched=CLOCK):
    return Story(
        item_id,
        item_id,
        f"https://example.org/{item_id}",
        "",
        fetched,
        START,
        False,
        score,
        score if alias is None else alias,
        tier,
        "test",
    )


def test_replay_isolates_tier_floor_rank_and_capture_time():
    stories = [
        story("humana", 0.46, "exclude"),
        replace(
            story("review", 0.45, "exclude"),
            title="Ascension pulls back from insurance to focus on care delivery",
        ),
        story("noise", 0.7, "exclude"),
        story("low-score", 0.16, "display", alias=0.05),
        story("alias", 0.8, "brief", alias=0.24),
        story("eligible", 0.6, "brief"),
        story("alternate", 0.55, "brief"),
        story("late", 1.0, "brief", fetched=CLOCK + timedelta(minutes=1)),
        replace(story("duplicate", 1.0, "brief"), duplicate=True),
    ]
    evidence = {
        "humana": Evidence(
            "humana", START, "confirmed_context", "https://example.org", "senior care"
        ),
        **{
            item_id: Evidence(
                "same-event",
                START,
                "confirmed_context",
                "https://example.org",
                "MA event",
            )
            for item_id in ("eligible", "alternate")
        },
    }
    config = AppConfig(digest_max_items=1, digest_min_score=0.3)
    result = evaluate(stories, evidence, [CLOCK], START, END, config)
    arms = result["arms"]
    at = CLOCK.isoformat()
    assert arms["baseline"]["briefings"][at]["selected"] == ["alias"]
    assert arms["alias_only"]["briefings"][at]["selected"] == ["noise"]
    assert arms["eligibility_only"]["briefings"][at]["selected"] == ["alias"]
    assert arms["combined"]["briefings"][at]["selected"] == ["eligible"]
    assert arms["combined"]["briefings"][at]["event_duplicates"] == ["alternate"]
    assert set(arms["combined"]["changes"]["feed"]["left"]) == {
        "noise",
        "review",
        "low-score",
    }
    assert arms["alias_only"]["changes"]["feed"]["left"] == ["low-score"]
    assert set(arms["eligibility_only"]["changes"]["feed"]["left"]) == {
        "noise",
        "review",
    }
    assert "humana" in arms["combined"]["feed"]
    assert "humana" not in arms["combined"]["drafts"]
    assert "duplicate" in arms["combined"]["drafts"]
    assert result["items"]["review"]["review"]
    assert not result["items"]["noise"]["review"]


def test_acceptance_requires_complete_blind_labels_and_checks_event_overlap():
    stories = [story("brief", 0.5, "brief"), story("noise", 0.6, "exclude")]
    config = AppConfig(digest_max_items=2, digest_min_score=0.3)
    result = evaluate(stories, {}, [CLOCK], START, END, config)
    labels = {
        key: {"label": label, "event_id": key, "reviewer": "editor", "blind": True}
        for key, label in (("brief", "relevant_brief"), ("noise", "irrelevant"))
    }
    prior = {"event_ids": ["earlier"], "items": [{"title": "earlier story"}]}
    assert (
        acceptance(stories, result, labels, prior, 0.1)["status"]
        == "passes_measured_floors"
    )
    with pytest.raises(ValueError, match="full labels"):
        acceptance(stories, result, {"brief": labels["brief"]}, prior, 0.1)
    with pytest.raises(ValueError, match="overlap"):
        acceptance(stories, result, labels, {**prior, "event_ids": ["brief"]}, 0.1)


def test_archive_rebuild_uses_alias_delta_and_original_breakdown(tmp_path):
    path = tmp_path / "archive.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE stories (item_id, title, link, summary, published_date, "
            "fetched_at, duplicate_of, relevance_score, entities, scoring_breakdown, eligibility_tier)"
        )
        db.execute("CREATE TABLE digests (generated_at)")
        db.execute("INSERT INTO digests VALUES (?)", (CLOCK.isoformat(),))
        db.execute(
            "INSERT INTO stories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "aliases",
                "UnitedHealth and UnitedHealthcare Medicare Advantage news",
                "https://example.org/aliases",
                "",
                None,
                START.isoformat(),
                None,
                0.4,
                '["UnitedHealth", "UnitedHealthcare"]',
                '[{"contribution": 0.4}]',
                None,
            ),
        )
    config = AppConfig()
    rows = load_archive(path, START, END, config)
    assert rows[0].published == START
    assert rows[0].tier == "exclude"  # Default AppConfig has no taxonomy vocabulary.
    assert rows[0].alias_score == 0.2
    assert archive_clocks(path, START, END) == [CLOCK]
