from evals.relevance.product_replay import run_replay


def test_product_replay_matches_declared_surface_outcomes():
    report = run_replay()
    assert report["passed"], report["results"]
    assert len(report["results"]) == 6
    assert report["archive_duplicates_assigned"] == 1
    assert report["alert_duplicates_suppressed"] == 1


def test_replay_exercises_production_paths_and_dates():
    report = run_replay()
    assert report["production_paths"]["scoring_and_eligibility"] == "score_items"
    assert report["production_paths"]["archive_duplicate_assignment"] == (
        "assign_story_duplicates"
    )
    assert report["production_paths"]["angles_cards"] == "build_angles"
    assert report["angles_card_count"] > 0
    assert all(row["event_date"] != row["first_seen"] for row in report["results"])
    assert all(row["metadata_round_trip"] for row in report["results"])
    assert all(
        row["metadata_sources"]
        == {
            "event_date": "stories.published_date",
            "first_seen": "stories.fetched_at",
        }
        for row in report["results"]
    )
    assert {row["rating_year"] for row in report["results"]} == {2027}


def test_replay_labels_usefulness_separately_from_public_availability():
    report = run_replay()
    assert {
        row["id"]: row["operational_usefulness"] for row in report["results"]
    } == {
        "pp2-must-catch": "must_catch",
        "pp2-unchanged-repeat": "noise",
        "pp2-material-update": "material_update",
        "dated-primary-analysis": "decision_support",
        "below-score-must-catch": "must_catch",
        "conflicting-figures": "requires_adjudication",
    }
    assert {row["public_availability"] for row in report["results"]} == {"public"}


def test_missing_claim_support_guard_is_reported_as_product_gap():
    report = run_replay()
    assert report["drafting_guard"]["available"] is False
    assert report["drafting_guard"]["status"] == "product_gap"
    conflict = next(r for r in report["results"] if r["id"] == "conflicting-figures")
    assert conflict["observed"]["alert"] is True
    assert "draft_candidate" not in conflict["observed"]

