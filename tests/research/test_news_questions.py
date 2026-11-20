"""Structural checks on the news-track question set (evals/news/questions.yaml).

These test the file, not any retriever: every referenced story exists in the
pinned snapshot, grades and fields follow protocol §5, and no item contradicts
itself. Offline and deterministic.
"""

from pathlib import Path

import pytest
import yaml

from ma_signal_monitor.research.news import snapshot as snap

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS = ROOT / "evals" / "news" / "questions.yaml"
SNAPSHOT = ROOT / "evals" / "news" / "snapshot"

TYPES = {
    "entity_ambiguity",
    "acronym",
    "terminology_drift",
    "distractor_rich",
    "multi_story",
    "natural_language",
    "negation",
    "conflicting_sources",
    "exact_numeric",
    "unanswerable",
}
FIELDS = {
    "id",
    "query",
    "question_type",
    "answerable",
    "expected_answer",
    "relevant",
    "distractors",
    "failure_mode_targeted",
    "query_source",
    "vetting",
    "note",
}


@pytest.fixture(scope="module")
def data():
    return yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def story_ids():
    return {s.item_id for s in snap.load_corpus(SNAPSHOT)}


def test_meta_points_at_the_committed_snapshot(data):
    manifest = snap.read_manifest(SNAPSHOT)
    assert data["_meta"]["snapshot_sha256"] == manifest["sha256"]
    assert data["_meta"]["grades"] == {
        2: "directly answers",
        1: "related, partially answers",
    }


def test_ids_are_unique_and_sequential(data):
    ids = [q["id"] for q in data["questions"]]
    assert ids == [f"news-dev-{i:03d}" for i in range(1, len(ids) + 1)]


def test_every_item_has_exactly_the_schema_fields(data):
    for q in data["questions"]:
        assert set(q) == FIELDS, q["id"]


def test_field_values_are_valid(data):
    for q in data["questions"]:
        assert q["question_type"] in TYPES, q["id"]
        assert q["query_source"] in {"drafted", "owner"}, q["id"]
        assert q["vetting"] in {"pending", "accepted", "edited", "rejected"}, q["id"]
        assert isinstance(q["query"], str) and q["query"].strip(), q["id"]


def test_answerable_matches_relevant_and_answer(data):
    for q in data["questions"]:
        if q["answerable"]:
            assert q["relevant"], q["id"]
            assert q["expected_answer"], q["id"]
            assert q["question_type"] != "unanswerable", q["id"]
        else:
            assert q["relevant"] == [], q["id"]
            assert q["expected_answer"] is None, q["id"]
            assert q["question_type"] == "unanswerable", q["id"]


def test_units_are_well_formed(data):
    for q in data["questions"]:
        for unit in q["relevant"]:
            assert set(unit) == {"stories", "grade"}, q["id"]
            assert unit["grade"] in {1, 2}, q["id"]
            assert unit["stories"], q["id"]
        if q["answerable"]:
            assert any(u["grade"] == 2 for u in q["relevant"]), q["id"]


def test_every_story_exists_in_the_snapshot(data, story_ids):
    for q in data["questions"]:
        refs = [s for u in q["relevant"] for s in u["stories"]] + q["distractors"]
        missing = [s for s in refs if s not in story_ids]
        assert not missing, (q["id"], missing)


def test_no_story_is_counted_twice_within_a_question(data):
    for q in data["questions"]:
        relevant = [s for u in q["relevant"] for s in u["stories"]]
        assert len(relevant) == len(set(relevant)), q["id"]
        assert not set(relevant) & set(q["distractors"]), q["id"]
        assert len(q["distractors"]) == len(set(q["distractors"])), q["id"]


def test_set_is_not_frozen_while_any_item_is_pending(data):
    pending = any(q["vetting"] == "pending" for q in data["questions"])
    if pending:
        assert "not frozen" in data["_meta"]["status"]
